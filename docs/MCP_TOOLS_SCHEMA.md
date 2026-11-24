# MSME-Incentive Saathi — FastMCP Servers, Tool Schemas & Permission Matrix

**Owner:** Tanmay (Team Member 2 — Knowledge, Memory & Tools Engineer)  
**Servers:** `msme_saathi/mcp_servers/` · **LangGraph Adapter:** `msme_saathi/tools/mcp_client.py` (`langchain-mcp-adapters`)

---

## 1. Server Overview & Transport Modes

All four servers are implemented with `FastMCP` (`mcp.server.fastmcp`), enforce structured error responses (`{"ok": False, "error": {"code": ..., "message": ...}}`) instead of uncaught crashes, and run over **`stdio`** in development/CI or **`streamable-http`** inside Docker Compose:

| FastMCP Server | Module | Default HTTP Port | Access Level | Backing Store |
|---|---|---:|---|---|
| `msme-policies-kb` | `msme_saathi.mcp_servers.policies_kb_server` | `8101` | **READ** | 42 schemes (168 clauses), BM25 + ChromaDB + Reranker |
| `rule-engine` | `msme_saathi.mcp_servers.rule_engine_server` | `8102` | **READ** | Deterministic Python over `data/rules/scheme_rules.json` |
| `doc-check` | `msme_saathi.mcp_servers.doc_check_server` | `8103` | **READ** | Deterministic parser & cross-document consistency checker |
| `mock-portal` | `msme_saathi.mcp_servers.mock_portal_server` | `8104` | **READ + WRITE** | SQLite `MockPortalDB` + FastAPI app (`portal_api.py`) |

---

## 2. Complete Tool List, Schemas & Per-Agent Access Matrix

> **Least-Privilege Write Isolation:** `submit_application` is the **only** `[WRITE]` tool in the system (`readOnlyHint=False, destructiveHint=True`) and is granted **exclusively** to `application_submit` / `application_agent` behind the human `interrupt()` approval gate.

| # | Server | Tool Name | Mode | Allowed Agents | Typed Inputs | Typed Output Fields | Dedicated Test |
|---:|---|---|---|---|---|---|---|
| 1 | `msme-policies-kb` | `search_policies` | `READ` | `eligibility_analyst` | `query: str, state?: str, district_tier?: str, enterprise_scale?: str, sector?: str, broadened: bool=False` | `list[{policy_id, scheme_name, state, clause_id, matched_passage_id, validity_date, collection_date, source_url, snippet, score}]` | `test_tool_search_policies_multilingual` |
| 2 | `msme-policies-kb` | `get_policy_clause` | `READ` | `eligibility_analyst`, `response_synthesizer` | `policy_id: str, clause_id: str` | `{ok, policy_id, scheme_name, clause_id, section, title, text, source_url, collection_date, validity_date, error?}` | `test_tool_get_policy_clause_and_structured_error` |
| 3 | `msme-policies-kb` | `get_document_checklist` | `READ` | `document_checker` | `policy_id: str` | `{ok, policy_id, scheme_name, authority, required_documents: list[str], source_url, collection_date, validity_date, error?}` | `test_tool_get_document_checklist_and_structured_error` |
| 4 | `rule-engine` | `evaluate_eligibility` | `READ` | `eligibility_analyst` | `profile: dict, policy_id: str` | `{ok, policy_id, verdict: "eligible"\|"not_eligible"\|"cant_tell", deciding_clause, missing_fields: list[str], estimated_subsidy_inr: float\|None, reason, error?}` | `test_tool_evaluate_eligibility_boundaries_and_cant_tell` |
| 5 | `rule-engine` | `get_policy_rules` | `READ` | `eligibility_analyst` | `policy_id: str` | `{ok, policy_id, scheme_name, gold_standard, human_reviewed, required_fields, conditions, subsidy_formula, error?}` | `test_tool_get_policy_rules` |
| 6 | `doc-check` | `extract_document_fields` | `READ` | `document_checker`, `intake_agent` | `document_id: str, document_type: str, raw_text: str` | `{ok, document_id, document_type, enterprise_name?, udyam_number?, gstin?, gst_state?, pan_masked?, document_date?, investment_lakhs?, error?}` | `test_tool_extract_document_fields_and_pan_masking` |
| 7 | `doc-check` | `compare_document_fields` | `READ` | `document_checker` | `profile: dict, uploaded_docs: list[dict], required_docs: list[str]` | `{ok, present_documents: list[str], missing_documents: list[str], mismatches: list[dict], requires_human_referral: bool, error?}` | `test_tool_compare_document_fields_flags_name_state_and_investment_mismatches` |
| 8 | `mock-portal` | `create_application_draft` | `READ` | `application_draft` | `enterprise_id: str, policy_id: str, profile: dict, revision: int=1` | `{ok, draft_id, policy_id, revision, payload_sha256, payload, error?}` | `test_tools_mock_portal_draft_submit_tamper_and_status` |
| 9 | `mock-portal` | `get_application_status` | `READ` | `intake_agent`, `application_draft`, `response_synthesizer` | `tracking_id: str` | `{ok, tracking_id, draft_id?, policy_id?, payload_sha256?, status, submitted_at?, error?}` | `test_tools_mock_portal_draft_submit_tamper_and_status` |
| 10 | `mock-portal` | `submit_application` | **`WRITE`** | **`application_submit` ONLY** | `draft_id: str, payload: dict, approval_token?: str` | `{ok, tracking_id?, draft_id, policy_id?, payload_sha256?, status, submitted_at?, error?}` | `test_tools_mock_portal_draft_submit_tamper_and_status` |

---

## 3. Running Servers Locally (`stdio`) & in Docker (`streamable-http`)

```bash
# Development (stdio — spawned automatically by MultiServerMCPClient)
python -m msme_saathi.mcp_servers.policies_kb_server --transport stdio
python -m msme_saathi.mcp_servers.rule_engine_server --transport stdio
python -m msme_saathi.mcp_servers.doc_check_server   --transport stdio
python -m msme_saathi.mcp_servers.mock_portal_server --transport stdio

# Docker / HTTP mode
python -m msme_saathi.mcp_servers.policies_kb_server --transport streamable-http --port 8101
python -m msme_saathi.mcp_servers.rule_engine_server --transport streamable-http --port 8102
python -m msme_saathi.mcp_servers.doc_check_server   --transport streamable-http --port 8103
python -m msme_saathi.mcp_servers.mock_portal_server --transport streamable-http --port 8104

# Standalone Mock Portal FastAPI REST Service
uvicorn msme_saathi.mcp_servers.portal_api:app --host 0.0.0.0 --port 8105
```
