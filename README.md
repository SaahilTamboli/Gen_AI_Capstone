# MSME-Incentive Saathi — LangGraph Skeleton & MCP Stubs (Days 2–3)

**Owner:** Team Member 3 (Agents & Orchestration Engineer)

Clean, modular LangGraph skeleton with SQLite checkpointing, visible plan + replanning, an `interrupt()` approval gate before `mock-portal.submit_application`, and lightweight MCP stubs so teammates can plug in their work directly.

---

## Codebase Structure

```text
msme_saathi/
├── state.py              # Typed EnterpriseProfile, ExecutionPlan, and MSMEState
├── checkpointer.py       # SQLite checkpointer (`SqliteSaver`) for crash/resume
├── graph.py              # StateGraph wiring, conditional routing, and Mermaid export
├── tools/
│   └── mcp_stubs.py      # Stub functions mirroring the 4 FastMCP servers (Team Member 2 plugs in here)
└── agents/
    ├── supervisor.py     # Visible plan + conditional routing + replanning branches
    ├── intake.py         # EnterpriseProfile intake + targeted follow-up question
    ├── eligibility.py    # Policy search + deterministic rule-engine calls
    ├── documents.py      # Document checklist + mismatch check
    └── application.py    # Draft -> Approval Gate `interrupt()` -> Submit -> Final reply (Team Member 4 plugs in here)
scripts/
└── demo.py               # End-to-end Akola Marathi demo with simulated SQLite restarts
tests/
└── test_graph.py         # Pytest suite (E2E, SQLite crash/resume, edit replan, reject, broaden replan)
```

---

## Where Teammates Plug In

- **Team Member 2 (Knowledge, Memory & Tools):** Replace the stub functions in `msme_saathi/tools/mcp_stubs.py` with `langchain-mcp-adapters` tools from the 4 FastMCP servers (`msme-policies-kb`, `rule-engine`, `doc-check`, `mock-portal`). Agent nodes already consume plain JSON dicts matching those signatures.
- **Team Member 4 (Control, Security & Deployment):** Plug payload SHA-256 hashing, approval-token validation, and FastAPI/UI `Command(resume=...)` into `approval_gate_node` (`msme_saathi/agents/application.py`), and wrap tool calls with pre/post-tool hooks.
- **Team Member 1 (Product & Evaluation):** Drive golden personas through `build_graph(checkpointer=...)` and inspect `verdicts`, `questions_asked`, `plan.replans`, and `submission_result`.

---

## Run Tests & Demo

```bash
python -m pytest -v
python -X utf8 scripts/demo.py
```
