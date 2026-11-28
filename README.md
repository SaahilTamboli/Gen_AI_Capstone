# MSME-Incentive Saathi — Industrial Capital Grant & Subsidy Navigator

Multi-agent welfare & MSME industrial incentive navigator in **English, Hindi, and Marathi**, built on the Capstone Reference Architecture (LangGraph orchestration, Hybrid BM25 + ChromaDB RRF retrieval, deterministic Python rule engine, 4 FastMCP servers, consent-gated long-term memory, and human-in-the-loop approval gate).

---

## Codebase Structure

```text
msme_saathi/
├── config.py                 # [Role 2 — Tanmay] `.env` loader & OpenRouter defaults (`qwen/qwen3-embedding-8b`, `openai/gpt-4.1-mini`)
├── state.py                  # Typed EnterpriseProfile, ExecutionPlan, and MSMEState
├── checkpointer.py           # SQLite checkpointer (`SqliteSaver`) for crash/resume
├── graph.py                  # StateGraph wiring, conditional routing, and Mermaid export
├── knowledge/                # [Role 2 — Tanmay] Ingestion, Hybrid Retrieval, Rule Engine & Doc Check
│   ├── models.py             # Typed clause chunks, rule conditions, subsidy formulas
│   ├── ingestion.py          # 42-scheme provenance check, official-domain allowlist & scam flagging
│   ├── query_rewriter.py     # EN/HI/MR colloquial query rewriting + Devanagari digit normalizer
│   ├── hybrid_retriever.py   # BM25 + Persistent ChromaDB (`qwen/qwen3-embedding-8b` + disk cache) + RRF + Reranker
│   ├── rule_engine.py        # Deterministic Python evaluator over 42 clause-linked JSON rules
│   ├── rule_extractor.py     # Prose-to-JSON rule extractor + 18 gold-standard accuracy benchmark
│   └── doc_checker.py        # Structured field extraction + cross-document mismatch verifier
├── memory/                   # [Role 2 — Tanmay] Short-term trimming & Long-term Memory Store
│   ├── store.py              # Consent-gated, HMAC-pseudonymous LangGraph + SQLite memory with TTL & Forget-Me
│   └── context_trimmer.py    # Short-term window trimmer & PII-scrubbed turn summarizer
├── mcp_servers/              # [Role 2 — Tanmay] 4 FastMCP Servers (`stdio` + Docker `streamable-http`)
│   ├── schemas.py            # Typed tool schemas + READ/WRITE per-agent permission matrix
│   ├── policies_kb_server.py # Server 1: `msme-policies-kb` [READ]
│   ├── rule_engine_server.py # Server 2: `rule-engine`      [READ]
│   ├── doc_check_server.py   # Server 3: `doc-check`        [READ]
│   ├── portal_db.py          # SQLite DB with SHA-256 payload hashing & tamper invalidation
│   ├── portal_api.py         # Standalone FastAPI REST app for `mock-portal`
│   └── mock_portal_server.py # Server 4: `mock-portal`      [draft/status=READ, submit=WRITE]
├── tools/
│   ├── mcp_client.py         # `langchain-mcp-adapters` MultiServerMCPClient + agent permission gate
│   └── mcp_stubs.py          # Unified bindings wired to the 4 production FastMCP implementations
└── agents/                   # [Role 3] Supervisor + Specialist LangGraph nodes
    ├── supervisor.py         # Visible plan + conditional routing + replanning branches
    ├── intake.py             # EnterpriseProfile intake + targeted follow-up question
    ├── eligibility.py        # Policy search + deterministic rule-engine calls
    ├── documents.py          # Document checklist + mismatch check
    └── application.py        # Draft -> Approval Gate `interrupt()` -> Submit -> Final reply
data/
├── schemes/                  # 42 official schemes (168 clauses) + planted scam page for security audit
├── rules/                    # 42 clause-linked JSON rule sets (18 human-reviewed gold-standard)
└── eval/                     # 36 labelled EN/HI/MR queries + Recall@5 & Memory-On/Off results
docs/
├── MEMORY_POLICY.md          # One-page Memory Policy + field justifications + Memory-On/Off table
├── MCP_TOOLS_SCHEMA.md       # Complete 10-tool schema catalog & READ/WRITE agent access matrix
├── RETRIEVAL_EVALUATION.md   # Hybrid retrieval design & Recall@5 before/after report (69.4% -> 100%)
└── CONTRIBUTION_LOG.md       # Team role ownership & milestone log
scripts/
├── build_corpus.py           # Rebuilds corpus, rules, scam test pages & 36 labelled queries
├── eval_retrieval.py         # Measures Recall@5 & MRR@5 before and after hybrid RRF + query rewrite
├── compare_memory.py         # Runs Memory-On vs. Memory-Off + Forget-Me verification
└── demo.py                   # End-to-end Akola Marathi demo with simulated SQLite restarts
```

---

## Team Integration Points

- **Team Member 2 — Tanmay (Knowledge, Memory & Tools):** Completed 42-scheme ingestion (`168` clauses), hybrid BM25 + persistent ChromaDB (`qwen/qwen3-embedding-8b` with SQLite disk cache + offline fallback) + RRF + reranker + EN/HI/MR query rewriting, deterministic JSON `rule-engine`, `doc-check`, SQLite + FastAPI `mock-portal`, all 4 FastMCP servers (`stdio` & `streamable-http`), `langchain-mcp-adapters` loader, and consent-gated `EnterpriseMemoryStore` (`docs/MEMORY_POLICY.md`).
- **Team Member 3 (Agents & Orchestration):** LangGraph supervisor/specialists consume live outputs seamlessly via `msme_saathi/tools/` (or async ReAct `load_agent_mcp_tools(agent_name)`), plus `rule_extractor.py` for Signature Challenge A's Reflexion loop.
- **Team Member 4 — Karthik (Control, Security & Deployment):** Can wire `check_tool_permission(agent, tool)` into pre-tool hooks, use `make_approval_token(draft_id, payload)` / `payload_sha256` from `portal_db.py` in `approval_gate_node`, and run the 4 FastMCP servers over `--transport streamable-http` in Docker Compose.
- **Team Member 1 (Product & Evaluation):** Can evaluate golden personas against all 42 schemes and inspect flagged scam hits via `retriever.inspect_flagged_scam_matches(query)`.

---

## Quickstart: Run Tests, Benchmarks & Demo

```bash
cp .env.example .env          # Add your personal OPENROUTER_API_KEY (gitignored)
pip install -e ".[dev]"
python -m pytest -v
python scripts/eval_retrieval.py
python scripts/compare_memory.py
python -X utf8 scripts/demo.py
```
