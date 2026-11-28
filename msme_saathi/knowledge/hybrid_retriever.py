"""Hybrid Policy Retriever: BM25 + ChromaDB Vectors + Reciprocal Rank Fusion (RRF) + Reranker.

Implements the Capstone Guide retrieval stack:
  - Lexical index: `rank_bm25.BM25Okapi` over multilingual clause chunks + scheme keywords
  - Vector store: Persistent `chromadb` collection (`data/chroma/`) backed by OpenRouter
    multilingual embeddings (`qwen/qwen3-embedding-8b` by default) with SQLite disk caching
    and automatic deterministic offline fallback when no API key is configured
  - Fusion: Reciprocal Rank Fusion (RRF, k=60)
  - Reranker: Cross-attention lexical + metadata alignment reranker (with optional SentenceTransformers CrossEncoder)
  - Security: Official-source allowlist enforcement (`is_official_source=True`) and scam-page flagging
"""

from __future__ import annotations

from array import array
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
import httpx
from rank_bm25 import BM25Okapi

from msme_saathi.config import (
    get_chroma_persist_dir,
    get_embedding_model,
    get_openrouter_api_key,
    get_openrouter_base_url,
    use_openrouter_embeddings,
)
from msme_saathi.knowledge.ingestion import DEFAULT_CORPUS_PATH, PolicyCorpusIngestor
from msme_saathi.knowledge.models import FlaggedSourceRecord, PolicyClauseChunk
from msme_saathi.knowledge.query_rewriter import normalize_digits, rewrite_query

_TOKEN_RE = re.compile(r"[\w\u0900-\u097F]+", re.UNICODE)
_UNSET: Any = object()


class _OpenRouterEmbeddingError(RuntimeError):
    """Raised internally when OpenRouter embedding inference is unavailable."""


def tokenize_multilingual(text: str) -> list[str]:
    """Tokenize English, Hindi, and Marathi text preserving Devanagari matras and policy numbers."""
    clean = normalize_digits(text.lower())
    tokens = _TOKEN_RE.findall(clean)
    return [t for t in tokens if len(t) > 1 or t.isdigit()]


def _slugify_for_collection(value: str, max_len: int = 30) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return (slug or "embed")[:max_len].strip("_") or "embed"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingDiskCache:
    """Persistent SQLite float32 vector cache keyed by `(model, text_sha256)`.

    Prevents redundant OpenRouter embedding API calls across repeated queries,
    MCP server restarts, evaluation runs, and pytest executions.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    model TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector_blob BLOB NOT NULL,
                    PRIMARY KEY (model, text_sha256)
                )
                """
            )
            self._conn.commit()

    def get_many(self, model: str, hashes: list[str]) -> dict[str, list[float]]:
        if not hashes:
            return {}
        unique_hashes = list(dict.fromkeys(hashes))
        found: dict[str, list[float]] = {}
        with self._lock:
            for start in range(0, len(unique_hashes), 200):
                batch = unique_hashes[start : start + 200]
                placeholders = ",".join("?" for _ in batch)
                cur = self._conn.execute(
                    f"SELECT text_sha256, vector_blob FROM embedding_cache WHERE model = ? AND text_sha256 IN ({placeholders})",
                    [model, *batch],
                )
                for text_hash, blob in cur.fetchall():
                    vec = array("f")
                    vec.frombytes(blob)
                    found[text_hash] = vec.tolist()
        return found

    def put_many(self, model: str, items: dict[str, list[float]]) -> None:
        if not items:
            return
        rows = []
        for text_hash, vector in items.items():
            buf = array("f", (float(x) for x in vector))
            rows.append((model, text_hash, len(vector), buf.tobytes()))
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO embedding_cache (model, text_sha256, dim, vector_blob)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(model, text_sha256) DO UPDATE SET
                    dim = excluded.dim,
                    vector_blob = excluded.vector_blob
                """,
                rows,
            )
            self._conn.commit()

    def count(self, model: str | None = None) -> int:
        with self._lock:
            if model is None:
                row = self._conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM embedding_cache WHERE model = ?",
                    (model,),
                ).fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class MultilingualDenseEmbedder(EmbeddingFunction[Documents]):
    """Multilingual ChromaDB embedder powered by OpenRouter (`qwen/qwen3-embedding-8b`) with disk cache.

    - When OpenRouter embeddings are enabled (`OPENROUTER_API_KEY` present and
      `MSME_USE_OPENROUTER_EMBEDDINGS != 0`), calls OpenRouter's OpenAI-compatible
      `/embeddings` endpoint in batches and persists vectors to `embedding_cache.sqlite`.
    - When offline or disabled (e.g., CI without API keys), cleanly uses a fast
      deterministic 256-dim multilingual word/character n-gram embedder.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        use_openrouter: bool | None = None,
        cache_dir: str | Path | None = None,
        dim: int = 256,
        batch_size: int = 32,
        strict_openrouter: bool = False,
    ) -> None:
        self.model = model or get_embedding_model()
        self.use_openrouter = use_openrouter_embeddings() if use_openrouter is None else bool(use_openrouter)
        self.dim = dim
        self.batch_size = max(1, int(batch_size))
        self.strict_openrouter = strict_openrouter
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._disk_cache = EmbeddingDiskCache(self.cache_dir / "embedding_cache.sqlite") if self.cache_dir else None

        # Telemetry counters (helpful for caching verification & eval reports)
        self.api_calls = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.last_backend: str = "openrouter" if self.use_openrouter else "offline_hash"

    @property
    def active_model_id(self) -> str:
        return self.model if self.use_openrouter else f"offline-hash-{self.dim}"

    @staticmethod
    def name() -> str:
        return "msme_multilingual_dense_v2"

    def get_config(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "dim": self.dim,
            "use_openrouter": self.use_openrouter,
        }

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "MultilingualDenseEmbedder":
        return MultilingualDenseEmbedder(
            model=str(config.get("model", get_embedding_model())),
            dim=int(config.get("dim", 256)),
            use_openrouter=bool(config.get("use_openrouter", False)),
        )

    def __call__(self, input: Documents) -> Embeddings:
        texts = [str(doc) for doc in input]
        if not texts:
            return []

        if self.use_openrouter:
            api_key = get_openrouter_api_key()
            if api_key:
                remote = self._embed_via_openrouter_cached(texts, api_key)
                if remote is not None:
                    self.last_backend = "openrouter"
                    return remote
            if self.strict_openrouter:
                raise _OpenRouterEmbeddingError(
                    f"OpenRouter embedding request failed for model '{self.model}'."
                )

        self.last_backend = "offline_hash"
        return [self._embed_single_offline(doc) for doc in texts]

    def _embed_via_openrouter_cached(self, texts: list[str], api_key: str) -> list[list[float]] | None:
        hashes = [_sha256_text(t) for t in texts]
        cached = self._disk_cache.get_many(self.model, hashes) if self._disk_cache else {}

        missing_by_hash: dict[str, str] = {}
        for text, h in zip(texts, hashes):
            if h in cached:
                self.cache_hits += 1
            else:
                self.cache_misses += 1
                missing_by_hash.setdefault(h, text)

        if missing_by_hash:
            missing_hashes = list(missing_by_hash.keys())
            missing_texts = [missing_by_hash[h] for h in missing_hashes]
            fetched = self._call_openrouter_embeddings(missing_texts, api_key)
            if fetched is None or len(fetched) != len(missing_texts):
                return None
            newly_cached = dict(zip(missing_hashes, fetched))
            if self._disk_cache is not None:
                self._disk_cache.put_many(self.model, newly_cached)
            cached.update(newly_cached)

        return [cached[h] for h in hashes]

    def _call_openrouter_embeddings(self, texts: list[str], api_key: str) -> list[list[float]] | None:
        endpoint = f"{get_openrouter_base_url()}/embeddings"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        all_vectors: list[list[float]] = []
        try:
            with httpx.Client(timeout=30.0) as client:
                for start in range(0, len(texts), self.batch_size):
                    batch = texts[start : start + self.batch_size]
                    resp = client.post(
                        endpoint,
                        headers=headers,
                        json={"model": self.model, "input": batch},
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    data = payload.get("data")
                    if not isinstance(data, list) or len(data) != len(batch):
                        return None
                    ordered = sorted(data, key=lambda item: item["index"])
                    all_vectors.extend([float(x) for x in item["embedding"]] for item in ordered)
                    self.api_calls += 1
            return all_vectors
        except Exception:
            return None

    def _embed_single_offline(self, text: str) -> list[float]:
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
        persist_directory: str | Path | None = _UNSET,
        include_untrusted_for_audit: bool = True,
        include_multilingual_keywords: bool = True,
        use_openrouter: bool | None = None,
        embedding_model: str | None = None,
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

        index_docs = [self._index_text(c) for c in self.chunks]

        # 1. Build BM25 index
        corpus_tokens = [tokenize_multilingual(doc) for doc in index_docs]
        self._bm25 = BM25Okapi(corpus_tokens)

        # 2. Configure persistent (or ephemeral) ChromaDB client
        if persist_directory is _UNSET:
            resolved_dir = get_chroma_persist_dir()
        else:
            resolved_dir = Path(persist_directory) if persist_directory is not None else None
        self.persist_directory: Path | None = resolved_dir

        if self.persist_directory is not None:
            self.persist_directory.mkdir(parents=True, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(path=str(self.persist_directory))
        else:
            self._chroma_client = chromadb.EphemeralClient()

        # 3. Build or reuse persistent ChromaDB vector collection (keyed by model + corpus fingerprint)
        want_openrouter = use_openrouter_embeddings() if use_openrouter is None else bool(use_openrouter)
        chosen_model = embedding_model or get_embedding_model()
        self.index_loaded_from_cache = False

        self._embedder = MultilingualDenseEmbedder(
            model=chosen_model,
            use_openrouter=want_openrouter,
            cache_dir=self.persist_directory,
            strict_openrouter=want_openrouter,
        )
        try:
            self._collection, self.index_loaded_from_cache = self._init_chroma_collection(index_docs)
        except _OpenRouterEmbeddingError:
            # Graceful fallback to offline deterministic collection if OpenRouter is unreachable
            self._embedder = MultilingualDenseEmbedder(
                model=chosen_model,
                use_openrouter=False,
                cache_dir=self.persist_directory,
                strict_openrouter=False,
            )
            self._collection, self.index_loaded_from_cache = self._init_chroma_collection(index_docs)

        self.embedding_model_id = self._embedder.active_model_id
        self._reranker = CrossEncoderReranker()

    def _index_text(self, chunk: PolicyClauseChunk) -> str:
        if self.include_multilingual_keywords:
            return CrossEncoderReranker._chunk_searchable_text(chunk)
        return f"{chunk.policy_id} {chunk.scheme_name} {chunk.title} {chunk.text} {chunk.snippet}"

    def _compute_corpus_fingerprint(self, index_docs: list[str]) -> str:
        hasher = hashlib.sha256()
        hasher.update(self._embedder.active_model_id.encode("utf-8"))
        hasher.update(b"|kw=")
        hasher.update(b"1" if self.include_multilingual_keywords else b"0")
        for chunk, doc in zip(self.chunks, index_docs):
            hasher.update(b"\n")
            hasher.update(chunk.chunk_id.encode("utf-8"))
            hasher.update(b":")
            hasher.update(doc.encode("utf-8"))
        return hasher.hexdigest()[:12]

    def _init_chroma_collection(self, index_docs: list[str]) -> tuple[Any, bool]:
        fingerprint = self._compute_corpus_fingerprint(index_docs)
        model_slug = _slugify_for_collection(self._embedder.active_model_id, max_len=30)
        collection_name = f"msme_{model_slug}_kw{int(self.include_multilingual_keywords)}_{fingerprint}"
        metadata = {
            "hnsw:space": "cosine",
            "corpus_fingerprint": fingerprint,
            "embedding_model": self._embedder.active_model_id,
        }

        collection = self._chroma_client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embedder,
            metadata=metadata,
        )
        if collection.count() == len(self.chunks):
            return collection, True

        if collection.count() > 0:
            self._chroma_client.delete_collection(collection_name)
            collection = self._chroma_client.create_collection(
                name=collection_name,
                embedding_function=self._embedder,
                metadata=metadata,
            )

        collection.add(
            ids=[c.chunk_id for c in self.chunks],
            documents=index_docs,
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
        return collection, False

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
            try:
                n_vec = min(40, len(self.chunks))
                vec_res = self._collection.query(query_texts=[effective_query], n_results=n_vec)
                raw_vec_ids = vec_res["ids"][0] if vec_res and vec_res.get("ids") else []
                vector_ids = [cid for cid in raw_vec_ids if cid in allowed_chunk_ids]
                fused = reciprocal_rank_fusion(bm25_ids, vector_ids, k=60)
            except Exception:
                fused = {cid: 1.0 / (60 + rank) for rank, cid in enumerate(bm25_ids, start=1)}
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
