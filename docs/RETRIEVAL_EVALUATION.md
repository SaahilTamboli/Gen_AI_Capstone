# MSME-Incentive Saathi — Ingestion, Hybrid Retrieval & Recall@5 Report

**Owner:** Tanmay (Team Member 2 — Knowledge, Memory & Tools Engineer)  
**Reproduce:** `python scripts/build_corpus.py && python scripts/eval_retrieval.py`

---

## 1. Corpus & Provenance Summary

- **Total Official Schemes Indexed:** **42 MSME & Industrial Schemes** (**22 Maharashtra State** schemes across PSI-2019, MEDA Rooftop Solar, CMEGP, Agro-Processing, Textiles, EV/Auto, Women & SC/ST Special Packages, Stamp Duty, Power Tariff, MPCB ZLD/ETP; **20 Central Government** schemes across CLCSS-TUS, PMEGP, CGTMSE, ZED, Lean, PMFME, PM Vishwakarma, Stand-Up India, SIDBI 4E, SFURTI, AHIDF).
- **Clause-Level Chunking:** **168 numbered clause passages** (`4` structured clauses per scheme: Eligibility Criteria, Operative Period, Quantum of Assistance, Mandatory Documentation).
- **Retained Metadata:** Every chunk preserves `policy_id`, `clause_id`, `state`, `authority`, `source_url` (HTTPS official `.gov.in` / `.nic.in` / nodal domain), `collection_date` (`2026-10-24`), and `validity_date`.
- **Security & Scam Source Filtering:** `PolicyCorpusIngestor` (`msme_saathi/knowledge/ingestion.py`) enforces `TRUSTED_OFFICIAL_DOMAINS` and scans for UPI fee / prompt-injection patterns. The planted scam page `SCAM-UPI-FASTTRACK-2026` (`http://msme-instant-grant-fasttrack.xyz/apply`, asking for a ₹500 UPI fee + `SYSTEM INSTRUCTION` override) is automatically flagged and excluded from active retrieval.

---

## 2. Hybrid Retrieval Pipeline

1. **Query-Side Technique (`query_rewriter.py`):** Normalizes Devanagari numerals (`३५` -> `35`) and expands colloquial Marathi, Hindi, and English entrepreneur expressions (e.g., *"विनातारण बँक कर्ज"*, *"सोलर पॅनेल्स अनुदान"*, *"बिना गारंटी लोन"*) into statutory MSME scheme vocabulary (with optional OpenRouter LLM query expansion).
2. **Lexical Index:** `rank_bm25.BM25Okapi` with Unicode Devanagari + Latin tokenization.
3. **Dense Vector Index:** `chromadb` cosine collection with 256-dim multilingual word/character n-gram embeddings (or OpenRouter `baai/bge-m3` embeddings via `MSME_USE_OPENROUTER_EMBEDDINGS=1`).
4. **Reciprocal Rank Fusion (RRF):** Combines BM25 and ChromaDB rankings via $\text{RRF}(d) = \sum_{r} \frac{1}{60 + \text{rank}_r(d)}$.
5. **Cross-Encoder Reranker (`CrossEncoderReranker`):** Re-scores top-30 RRF clause candidates using multi-word domain phrase alignment, title overlap, and profile metadata (`state`, `taluka_zone`, `enterprise_scale`, `sector`), citing exact `clause_id` passages.

---

## 3. Recall@5 Benchmark on 36 Labelled Queries (Before vs. After)

Evaluated on `data/eval/labelled_queries.jsonl` (**36 queries**: 12 English, 12 Hindi, 12 Marathi):

| Configuration | Overall Recall@1 | Overall Recall@5 | MRR@5 | EN Recall@5 | HI Recall@5 | MR Recall@5 |
|---|---:|---:|---:|---:|---:|---:|
| **A. Baseline** (BM25 on English policy prose only) | 36.11% | **69.44%** | 0.4819 | 100.00% | 50.00% | 58.33% |
| **B. + Multilingual Query Rewriting** | 97.22% | **100.00%** | 0.9861 | 100.00% | 100.00% | 100.00% |
| **C. Full Hybrid Stack** (BM25 + Chroma RRF + Query Rewrite + Reranker) | **91.67%** | **100.00%** | **0.9583** | **100.00%** | **100.00%** | **100.00%** |

- **Key Finding:** Raw English policy clauses fail on half of colloquial Hindi (`50.00%`) and Marathi (`58.33%`) entrepreneur queries due to script and vocabulary mismatch. Adding multilingual query rewriting + hybrid BM25/Chroma RRF + reranking lifts **Recall@5 from 69.44% to 100.00%** across all three languages (+30.56 percentage points).
- **Signature Challenge A Baseline (`rule_extractor.py`):** Evaluated against **18 hand-coded gold-standard reviewed schemes** in `data/rules/scheme_rules.json`, achieving **100% core condition extraction accuracy** while keeping `human_reviewed=False` on newly extracted rules until human sign-off.
