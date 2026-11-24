"""Hybrid Policy Retriever: BM25 + ChromaDB Vectors + Reciprocal Rank Fusion (RRF) + Reranker.

Implements the Capstone Guide retrieval stack:
  - Lexical index: `rank_bm25.BM25Okapi` over multilingual clause chunks + scheme keywords
  - Vector store: `chromadb` collection with dense multilingual n-gram/concept embeddings
    (or OpenRouter `baai/bge-m3` embeddings when `MSME_USE_OPENROUTER_EMBEDDINGS=1`)
  - Fusion: Reciprocal Rank Fusion (RRF, k=60)
  - Reranker: Cross-attention lexical + metadata alignment reranker (with optional SentenceTransformers CrossEncoder)
  - Security: Official-source allowlist enforcement (`is_official_source=True`) and scam-page flagging
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from rank_bm25 import BM25Okapi

from msme_saathi.knowledge.ingestion import DEFAULT_CORPUS_PATH, PolicyCorpusIngestor
from msme_saathi.knowledge.models import FlaggedSourceRecord, PolicyClauseChunk
from msme_saathi.knowledge.query_rewriter import normalize_digits, rewrite_query

_TOKEN_RE = re.compile(r"[\w\u0900-\u097F]+", re.UNICODE)


def tokenize_multilingual(text: str) -> list[str]:
    """Tokenize English, Hindi, and Marathi text preserving Devanagari matras and policy numbers."""
    clean = normalize_digits(text.lower())
    tokens = _TOKEN_RE.findall(clean)
    return [t for t in tokens if len(t) > 1 or t.isdigit()]


class MultilingualDenseEmbedder(EmbeddingFunction[Documents]):
    """Fast, deterministic 256-dim multilingual subword + concept embedder for ChromaDB.

    Works 100% offline in CI and clean laptops with zero multi-GB downloads, while smoothly
    switching to OpenRouter `baai/bge-m3` if `MSME_USE_OPENROUTER_EMBEDDINGS=1` is enabled.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    @staticmethod
    def name() -> str:
        return "msme_multilingual_dense_v1"

    def get_config(self) -> dict[str, Any]:
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "MultilingualDenseEmbedder":
        return MultilingualDenseEmbedder(dim=int(config.get("dim", 256)))

    def __call__(self, input: Documents) -> Embeddings:
        if os.getenv("MSME_USE_OPENROUTER_EMBEDDINGS") == "1" and os.getenv("OPENROUTER_API_KEY"):
            remote = self._try_openrouter_bge_m3(list(input))
            if remote is not None:
                return remote
        return [self._embed_single(doc) for doc in input]

    def _embed_single(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize_multilingual(text)
        # Word unigrams, bigrams, and character 4-grams capture cross-script and morphological similarity
        features: list[tuple[str, float]] = []
        for tok in tokens:
            features.append((f"w:{tok}", 2.0))
            if len(tok) >= 4:
                for i in range(len(tok) - 3):
                    features.append((f"c4:{tok[i:i+4]}", 0.6))
        for i in range(len(tokens) - 1):
            features.append((f"bg:{tokens[i]}_{tokens[i+1]}", 1.5))

        for feat, weight in features:
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if (digest[4] & 1) == 0 else -1.0
            vec[idx] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    @staticmethod
    def _try_openrouter_bge_m3(texts: list[str]) -> list[list[float]] | None:
        import httpx

        try:
            resp = httpx.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                json={"model": "baai/bge-m3", "input": texts},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
        except Exception:
            return None


class CrossEncoderReranker:
    """Scores `(query, clause_chunk)` pairs combining exact concept overlap, keyword alignment, and optional CrossEncoder."""

    def __init__(self) -> None:
        self._hf_model: Any = None
        model_name = os.getenv("MSME_CROSS_ENCODER_MODEL")
        if model_name:
            try:
                from sentence_transformers import CrossEncoder  # type: ignore

                self._hf_model = CrossEncoder(model_name)
            except Exception:
                self._hf_model = None

    def rerank(
        self,
        rewritten_query: str,
        candidates: list[tuple[PolicyClauseChunk, float]],
        *,
        state: str | None = None,
        district_tier: str | None = None,
        enterprise_scale: str | None = None,
        sector: str | None = None,
    ) -> list[tuple[PolicyClauseChunk, float]]:
        if not candidates:
            return []

        if self._hf_model is not None:
            pairs = [(rewritten_query, self._chunk_searchable_text(c)) for c, _ in candidates]
            scores = self._hf_model.predict(pairs)
            ranked = [
                (chunk, float(ce_score) + 0.25 * rrf_score)
                for (chunk, rrf_score), ce_score in zip(candidates, scores)
            ]
            return sorted(ranked, key=lambda x: x[1], reverse=True)

        q_tokens = set(tokenize_multilingual(rewritten_query))
        q_lower = rewritten_query.lower()

        reranked: list[tuple[PolicyClauseChunk, float]] = []
        for chunk, rrf_score in candidates:
            doc_text = self._chunk_searchable_text(chunk)
            d_tokens = set(tokenize_multilingual(doc_text))
            overlap = len(q_tokens & d_tokens) / max(len(q_tokens), 1)

            kw_score = sum(
                0.25 + 0.18 * len(kw.split())
                for kw in (*chunk.keywords_en, *chunk.keywords_hi, *chunk.keywords_mr)
                if kw.lower() in q_lower
            )
            title_hits = sum(1 for t in tokenize_multilingual(chunk.scheme_name) if t in q_tokens)
            meta_boost = 0.0
            if state and chunk.state.lower() == state.lower():
                meta_boost += 0.12
            if district_tier and district_tier in chunk.taluka_zones:
                meta_boost += 0.06
            if enterprise_scale and enterprise_scale.lower() in chunk.eligible_scales:
                meta_boost += 0.06
            if sector and (sector in chunk.target_sectors or "all_manufacturing" in chunk.target_sectors):
                meta_boost += 0.08
            if chunk.clause_id == chunk.primary_clause_id:
                meta_boost += 0.05

            final_score = (rrf_score * 12.0) + (overlap * 2.2) + kw_score + (title_hits * 0.20) + meta_boost
            reranked.append((chunk, final_score))

        return sorted(reranked, key=lambda x: x[1], reverse=True)

    @staticmethod
    def _chunk_searchable_text(chunk: PolicyClauseChunk) -> str:
        kws = " ".join([*chunk.keywords_en, *chunk.keywords_hi, *chunk.keywords_mr])
        sectors = " ".join(chunk.target_sectors)
        return f"{chunk.policy_id} {chunk.scheme_name} {chunk.title} {chunk.text} {chunk.snippet} {kws} {sectors}"


def reciprocal_rank_fusion(
    bm25_ids: list[str],
    vector_ids: list[str],
    k: int = 60,
) -> dict[str, float]:
    """Combine BM25 and dense vector rankings using Reciprocal Rank Fusion: sum(1 / (k + rank))."""
    scores: dict[str, float] = {}
    for rank, doc_id in enumerate(bm25_ids, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    for rank, doc_id in enumerate(vector_ids, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


class HybridPolicyRetriever:
    """End-to-end hybrid retriever over official MSME scheme clauses."""

    def __init__(
        self,
        corpus_path: str | Path = DEFAULT_CORPUS_PATH,
        persist_directory: str | Path | None = None,
        include_untrusted_for_audit: bool = True,
        include_multilingual_keywords: bool = True,
    ) -> None:
        self.include_multilingual_keywords = include_multilingual_keywords
        self.ingestor = PolicyCorpusIngestor(
            corpus_path=corpus_path,
            include_untrusted_for_audit=include_untrusted_for_audit,
        )
        self.report = self.ingestor.ingest()
        self.chunks: list[PolicyClauseChunk] = self.ingestor.chunks
        self.chunks_by_id: dict[str, PolicyClauseChunk] = {c.chunk_id: c for c in self.chunks}
        self.schemes_by_id = self.ingestor.schemes_by_id

        def _index_text(c: PolicyClauseChunk) -> str:
            if self.include_multilingual_keywords:
                return CrossEncoderReranker._chunk_searchable_text(c)
            return f"{c.policy_id} {c.scheme_name} {c.title} {c.text} {c.snippet}"

        # 1. Build BM25 index
        corpus_tokens = [tokenize_multilingual(_index_text(c)) for c in self.chunks]
        self._bm25 = BM25Okapi(corpus_tokens)

        # 2. Build ChromaDB vector index
        self._embedder = MultilingualDenseEmbedder(dim=256)
        if persist_directory:
            Path(persist_directory).mkdir(parents=True, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(path=str(persist_directory))
        else:
            self._chroma_client = chromadb.EphemeralClient()

        collection_name = f"msme_clauses_{len(self.chunks)}_{int(include_multilingual_keywords)}"
        try:
            self._chroma_client.delete_collection(collection_name)
        except Exception:
            pass
        self._collection = self._chroma_client.create_collection(
            name=collection_name,
            embedding_function=self._embedder,
            metadata={"hnsw:space": "cosine"},
        )
        self._collection.add(
            ids=[c.chunk_id for c in self.chunks],
            documents=[_index_text(c) for c in self.chunks],
            metadatas=[
                {
                    "policy_id": c.policy_id,
                    "state": c.state,
                    "clause_id": c.clause_id,
                    "collection_date": c.collection_date,
                    "validity_date": c.validity_date,
                    "is_official_source": c.is_official_source,
                }
                for c in self.chunks
            ],
        )
        self._reranker = CrossEncoderReranker()

    def search_policies(
        self,
        query: str,
        *,
        state: str | None = None,
        district_tier: str | None = None,
        enterprise_scale: str | None = None,
        sector: str | None = None,
        broadened: bool = False,
        top_k: int = 8,
        use_query_rewrite: bool = True,
        use_hybrid_vectors: bool = True,
        use_reranker: bool = True,
    ) -> list[dict[str, Any]]:
        """Retrieve ranked candidate schemes citing passage/clause IDs and provenance metadata."""
        effective_query = rewrite_query(query) if use_query_rewrite else normalize_digits(query)
        q_tokens = tokenize_multilingual(effective_query) or ["msme", "subsidy"]

        # State pre-filtering matches orchestration contract:
        # - Targeted search (`broadened=False` and `state` specified): match `chunk.state == state`
        # - Broadened or unspecified state: include all official state + central ("ALL") schemes
        allowed_chunk_ids: set[str] = set()
        for c in self.chunks:
            if not c.is_official_source:
                continue
            if not broadened and state:
                if c.state.lower() != state.lower():
                    continue
            allowed_chunk_ids.add(c.chunk_id)

        if not allowed_chunk_ids:
            return []

        # BM25 ranking
        bm25_scores = self._bm25.get_scores(q_tokens)
        bm25_pairs = [
            (self.chunks[i].chunk_id, float(bm25_scores[i]))
            for i in range(len(self.chunks))
            if self.chunks[i].chunk_id in allowed_chunk_ids
        ]
        bm25_pairs.sort(key=lambda x: x[1], reverse=True)
        bm25_ids = [cid for cid, _ in bm25_pairs[:40]]

        # Dense Vector ranking + RRF fusion
        if use_hybrid_vectors:
            n_vec = min(40, len(self.chunks))
            vec_res = self._collection.query(query_texts=[effective_query], n_results=n_vec)
            raw_vec_ids = vec_res["ids"][0] if vec_res and vec_res.get("ids") else []
            vector_ids = [cid for cid in raw_vec_ids if cid in allowed_chunk_ids]
            fused = reciprocal_rank_fusion(bm25_ids, vector_ids, k=60)
        else:
            fused = {cid: 1.0 / (60 + rank) for rank, cid in enumerate(bm25_ids, start=1)}

        top_candidates = sorted(
            [(self.chunks_by_id[cid], score) for cid, score in fused.items()],
            key=lambda x: x[1],
            reverse=True,
        )[:30]

        if use_reranker:
            ranked_chunks = self._reranker.rerank(
                effective_query,
                top_candidates,
                state=state,
                district_tier=district_tier,
                enterprise_scale=enterprise_scale,
                sector=sector,
            )
        else:
            ranked_chunks = top_candidates

        # Deduplicate by `policy_id`, keeping highest-ranked clause passage per scheme
        seen_policies: dict[str, dict[str, Any]] = {}
        for chunk, score in ranked_chunks:
            if chunk.policy_id in seen_policies:
                continue
            seen_policies[chunk.policy_id] = {
                "policy_id": chunk.policy_id,
                "scheme_name": chunk.scheme_name,
                "authority": chunk.authority,
                "state": chunk.state,
                "clause_id": chunk.primary_clause_id,
                "matched_passage_id": chunk.clause_id,
                "validity_date": chunk.validity_date,
                "collection_date": chunk.collection_date,
                "source_url": chunk.source_url,
                "snippet": chunk.snippet,
                "score": round(float(score), 4),
            }
            if len(seen_policies) >= top_k:
                break

        return list(seen_policies.values())

    def inspect_flagged_scam_matches(self, query: str) -> list[FlaggedSourceRecord]:
        """Return untrusted/scam sources that would have matched `query` if official-domain filtering were off."""
        q_tokens = set(tokenize_multilingual(rewrite_query(query)))
        matched_ids: set[str] = set()
        for u_chunk in self.ingestor.untrusted_chunks:
            u_tokens = set(tokenize_multilingual(CrossEncoderReranker._chunk_searchable_text(u_chunk)))
            if q_tokens & u_tokens:
                matched_ids.add(u_chunk.policy_id)
        return [self.ingestor.flagged_by_id[pid] for pid in sorted(matched_ids) if pid in self.ingestor.flagged_by_id]

    def get_policy_clause(self, policy_id: str, clause_id: str) -> dict[str, Any] | None:
        """Fetch an exact numbered clause with source URL and collection/validity dates."""
        for chunk in self.chunks:
            if chunk.policy_id == policy_id and (
                chunk.clause_id == clause_id or chunk.primary_clause_id == clause_id
            ):
                if chunk.clause_id == clause_id:
                    return {
                        "policy_id": chunk.policy_id,
                        "scheme_name": chunk.scheme_name,
                        "clause_id": chunk.clause_id,
                        "section": chunk.section,
                        "title": chunk.title,
                        "text": chunk.text,
                        "source_url": chunk.source_url,
                        "collection_date": chunk.collection_date,
                        "validity_date": chunk.validity_date,
                    }
        # Fallback to primary clause if caller requested generic clause reference
        scheme = self.schemes_by_id.get(policy_id)
        if not scheme:
            return None
        first_cl = scheme["clauses"][0]
        return {
            "policy_id": policy_id,
            "scheme_name": scheme["scheme_name"],
            "clause_id": clause_id or first_cl["clause_id"],
            "section": first_cl["section"],
            "title": first_cl["title"],
            "text": first_cl["text"],
            "source_url": scheme["source_url"],
            "collection_date": scheme["collection_date"],
            "validity_date": scheme["validity_date"],
        }

    def get_document_checklist(self, policy_id: str) -> dict[str, Any] | None:
        """Return mandatory document checklist and metadata for `policy_id`."""
        scheme = self.schemes_by_id.get(policy_id)
        if not scheme:
            return None
        return {
            "policy_id": policy_id,
            "scheme_name": scheme["scheme_name"],
            "authority": scheme["authority"],
            "required_documents": list(scheme["required_documents"]),
            "source_url": scheme["source_url"],
            "collection_date": scheme["collection_date"],
            "validity_date": scheme["validity_date"],
        }


_DEFAULT_RETRIEVER: HybridPolicyRetriever | None = None


def get_default_retriever() -> HybridPolicyRetriever:
    """Lazy singleton retriever shared by in-process adapters and FastMCP servers."""
    global _DEFAULT_RETRIEVER
    if _DEFAULT_RETRIEVER is None:
        _DEFAULT_RETRIEVER = HybridPolicyRetriever()
    return _DEFAULT_RETRIEVER
