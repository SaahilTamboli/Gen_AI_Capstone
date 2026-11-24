"""Evaluate Recall@5 and MRR@5 on 36 labelled multilingual queries (12 EN, 12 HI, 12 MR).

Measures retrieval quality BEFORE and AFTER changes (Capstone Guide Minimum Bar, page 3):
  1. Config A (Baseline): Lexical BM25 over English policy prose only (no query rewriting, no vectors, no reranker)
  2. Config B (+ Query Rewriting): BM25 + Multilingual EN/HI/MR colloquial query rewriting
  3. Config C (Full Hybrid Stack): BM25 + ChromaDB Dense Vectors (RRF k=60) + Multilingual Indexing/Rewriting + Reranker
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from msme_saathi.knowledge.hybrid_retriever import HybridPolicyRetriever

ROOT = Path(__file__).resolve().parents[1]
QUERIES_PATH = ROOT / "data" / "eval" / "labelled_queries.jsonl"
REPORT_JSON_PATH = ROOT / "data" / "eval" / "retrieval_eval_results.json"


def load_queries() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in QUERIES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def evaluate_config(
    retriever: HybridPolicyRetriever,
    queries: list[dict[str, Any]],
    *,
    use_query_rewrite: bool,
    use_hybrid_vectors: bool,
    use_reranker: bool,
    k: int = 5,
) -> dict[str, Any]:
    hits_at_k = 0
    hits_at_1 = 0
    rr_sum = 0.0
    by_lang: dict[str, list[int]] = defaultdict(list)

    for item in queries:
        results = retriever.search_policies(
            query=item["query"],
            state=item.get("state"),
            broadened=(item.get("state") is None),
            top_k=k,
            use_query_rewrite=use_query_rewrite,
            use_hybrid_vectors=use_hybrid_vectors,
            use_reranker=use_reranker,
        )
        ranked_ids = [r["policy_id"] for r in results[:k]]
        target = item["expected_policy_id"]
        hit = 1 if target in ranked_ids else 0
        hits_at_k += hit
        by_lang[item["language"]].append(hit)
        if ranked_ids and ranked_ids[0] == target:
            hits_at_1 += 1
        if target in ranked_ids:
            rr_sum += 1.0 / (ranked_ids.index(target) + 1)

    n = len(queries)
    return {
        "num_queries": n,
        "recall_at_1": round(hits_at_1 / n, 4),
        "recall_at_5": round(hits_at_k / n, 4),
        "mrr_at_5": round(rr_sum / n, 4),
        "recall_at_5_by_language": {
            lang: round(sum(vals) / len(vals), 4) for lang, vals in sorted(by_lang.items())
        },
    }


def main() -> None:
    queries = load_queries()
    baseline_retriever = HybridPolicyRetriever(include_multilingual_keywords=False)
    full_retriever = HybridPolicyRetriever(include_multilingual_keywords=True)

    cfg_a = evaluate_config(
        baseline_retriever,
        queries,
        use_query_rewrite=False,
        use_hybrid_vectors=False,
        use_reranker=False,
    )
    cfg_b = evaluate_config(
        baseline_retriever,
        queries,
        use_query_rewrite=True,
        use_hybrid_vectors=False,
        use_reranker=False,
    )
    cfg_c = evaluate_config(
        full_retriever,
        queries,
        use_query_rewrite=True,
        use_hybrid_vectors=True,
        use_reranker=True,
    )

    summary = {
        "dataset": str(QUERIES_PATH.relative_to(ROOT)),
        "total_labelled_queries": len(queries),
        "configurations": {
            "A_bm25_english_only_baseline": cfg_a,
            "B_plus_multilingual_query_rewriting": cfg_b,
            "C_full_hybrid_bm25_chroma_rrf_reranker": cfg_c,
        },
        "flagged_untrusted_sources": [f.model_dump() for f in full_retriever.report.flagged_sources],
    }
    REPORT_JSON_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== Retrieval Recall@5 Benchmark (36 Multilingual Queries: 12 EN, 12 HI, 12 MR) ===")
    for name, metrics in summary["configurations"].items():
        print(
            f"  {name:42s} | Recall@1={metrics['recall_at_1']:.2%} | "
            f"Recall@5={metrics['recall_at_5']:.2%} | MRR@5={metrics['mrr_at_5']:.4f} | "
            f"By Lang={metrics['recall_at_5_by_language']}"
        )
    print(f"  Flagged scam/untrusted sources blocked at ingestion: {len(summary['flagged_untrusted_sources'])}")


if __name__ == "__main__":
    main()
