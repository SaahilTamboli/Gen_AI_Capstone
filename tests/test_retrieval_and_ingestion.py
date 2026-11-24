"""Tests for corpus ingestion, scam/injection filtering, hybrid RRF retrieval, EN/HI/MR query rewriting, and Recall@5."""

from __future__ import annotations

import json
from pathlib import Path

from msme_saathi.knowledge import (
    HybridPolicyRetriever,
    PolicyCorpusIngestor,
    evaluate_extractor_against_gold,
    extract_rules_from_scheme_clauses,
    is_trusted_source_url,
    normalize_digits,
    reciprocal_rank_fusion,
    rewrite_query,
)

ROOT = Path(__file__).resolve().parents[1]


def test_corpus_ingestion_retains_metadata_and_flags_scam_pages():
    ingestor = PolicyCorpusIngestor(include_untrusted_for_audit=True)
    report = ingestor.ingest()

    assert report.official_schemes_indexed >= 40
    assert report.total_clause_chunks >= 160
    assert len(report.flagged_sources) >= 1
    assert any(f.policy_id == "SCAM-UPI-FASTTRACK-2026" for f in report.flagged_sources)

    # Every indexed official chunk must retain provenance URL, collection_date, validity_date, and state
    for chunk in ingestor.chunks:
        assert chunk.is_official_source is True
        assert is_trusted_source_url(chunk.source_url)
        assert chunk.collection_date == "2026-10-24"
        assert chunk.validity_date >= "2027-01-01"
        assert chunk.clause_id


def test_planted_upi_scam_page_is_flagged_and_excluded_from_search():
    retriever = HybridPolicyRetriever(include_untrusted_for_audit=True)
    results = retriever.search_policies(
        query="instant subsidy pay 500 processing fee upi capital grant",
        broadened=True,
    )
    returned_ids = {r["policy_id"] for r in results}
    assert "SCAM-UPI-FASTTRACK-2026" not in returned_ids

    flagged = retriever.inspect_flagged_scam_matches("pay 500 processing fee upi instant subsidy")
    assert any(f.policy_id == "SCAM-UPI-FASTTRACK-2026" for f in flagged)


def test_multilingual_query_rewriter_and_devanagari_digits():
    assert normalize_digits("₹३५ लाख गुंतवणूक") == "₹35 लाख गुंतवणूक"
    rewritten_mr = rewrite_query("कारखान्यावर सोलर पॅनेल्स बसवण्यासाठी अनुदान")
    assert "MH-GREEN-SOLAR-2024" in rewritten_mr or "rooftop solar" in rewritten_mr.lower()

    rewritten_hi = rewrite_query("बिना गारंटी बैंक लोन योजना")
    assert "CGTMSE" in rewritten_hi


def test_reciprocal_rank_fusion_combines_rankings():
    fused = reciprocal_rank_fusion(
        bm25_ids=["doc_a", "doc_b", "doc_c"],
        vector_ids=["doc_b", "doc_d", "doc_a"],
        k=60,
    )
    # `doc_b` is rank 2 in BM25 + rank 1 in Vectors -> beats `doc_c` and `doc_d`
    assert fused["doc_b"] > fused["doc_c"]
    assert fused["doc_a"] > fused["doc_d"]


def test_recall_at_5_improves_on_36_labelled_multilingual_queries():
    queries_path = ROOT / "data" / "eval" / "labelled_queries.jsonl"
    queries = [json.loads(line) for line in queries_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(queries) >= 30

    baseline = HybridPolicyRetriever(include_multilingual_keywords=False)
    improved = HybridPolicyRetriever(include_multilingual_keywords=True)

    base_hits = 0
    imp_hits = 0
    for q in queries:
        b_res = baseline.search_policies(
            q["query"],
            state=q.get("state"),
            broadened=(q.get("state") is None),
            top_k=5,
            use_query_rewrite=False,
            use_hybrid_vectors=False,
            use_reranker=False,
        )
        i_res = improved.search_policies(
            q["query"],
            state=q.get("state"),
            broadened=(q.get("state") is None),
            top_k=5,
            use_query_rewrite=True,
            use_hybrid_vectors=True,
            use_reranker=True,
        )
        if q["expected_policy_id"] in {r["policy_id"] for r in b_res}:
            base_hits += 1
        if q["expected_policy_id"] in {r["policy_id"] for r in i_res}:
            imp_hits += 1

    base_recall = base_hits / len(queries)
    imp_recall = imp_hits / len(queries)
    assert imp_recall > base_recall
    assert imp_recall >= 0.95


def test_prose_rule_extractor_and_gold_standard_benchmark():
    ingestor = PolicyCorpusIngestor()
    ingestor.ingest()
    psi_scheme = ingestor.schemes_by_id["MH-PSI-2019-CAP"]
    extracted = extract_rules_from_scheme_clauses(psi_scheme)
    assert extracted["human_reviewed"] is False
    assert extracted["subsidy_formula"]["rate_pct"] == 30.0
    assert extracted["subsidy_formula"]["cap_inr"] == 3_000_000.0

    bench = evaluate_extractor_against_gold()
    assert bench["gold_schemes_evaluated"] >= 15
    assert bench["condition_accuracy"] >= 0.85
