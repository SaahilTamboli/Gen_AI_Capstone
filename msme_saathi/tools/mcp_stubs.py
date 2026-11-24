"""Production tool bindings wired to Team Member 2's 4 FastMCP server implementations.

Supports two execution modes seamlessly while keeping 100% identical dict signatures for
LangGraph nodes (`msme_saathi/agents/*.py`) and pytest suites:
  - Default fast in-process mode calling the exact FastMCP server engines:
      1. `msme-policies-kb` [READ]  -> Hybrid BM25 + ChromaDB vectors + RRF + Reranker + EN/HI/MR query rewrite
      2. `rule-engine`      [READ]  -> Deterministic Python evaluation over 42 clause-linked JSON rules
      3. `doc-check`        [READ]  -> Structured field extraction + cross-document mismatch detection
      4. `mock-portal`      [WRITE] -> SQLite-backed draft + SHA-256 payload-verified submission
  - Live MCP wire-transport mode (`MSME_MCP_TRANSPORT=stdio` or `MSME_MCP_TRANSPORT=http`)
    routing calls over `langchain-mcp-adapters` (`MultiServerMCPClient`).
"""

from __future__ import annotations

import os
from typing import Any

from msme_saathi.knowledge.doc_checker import (
    compare_document_fields as _compare_docs_impl,
    extract_document_fields as _extract_docs_impl,
)
from msme_saathi.knowledge.hybrid_retriever import get_default_retriever
from msme_saathi.knowledge.rule_engine import get_default_rule_engine
from msme_saathi.mcp_servers.portal_db import get_default_portal_db
from msme_saathi.tools.mcp_client import call_mcp_tool_sync


def _wire_mode() -> str | None:
    val = os.getenv("MSME_MCP_TRANSPORT", "").strip().lower()
    return val if val in {"stdio", "http"} else None


def reset_portal_state() -> None:
    """Clear mock portal drafts and submissions between test runs."""
    get_default_portal_db().reset()


# ---------------------------------------------------------------------------
# 1. MCP Server: `msme-policies-kb` [READ]
# ---------------------------------------------------------------------------

def search_policies(
    query: str,
    state: str | None = None,
    district_tier: str | None = None,
    enterprise_scale: str | None = None,
    sector: str | None = None,
    broadened: bool = False,
) -> list[dict[str, Any]]:
    """Hybrid search (`BM25 + Chroma vectors + RRF + Reranker + EN/HI/MR query rewrite`)."""
    mode = _wire_mode()
    if mode:
        res = call_mcp_tool_sync(
            "msme-policies-kb",
            "search_policies",
            {
                "query": query,
                "state": state,
                "district_tier": district_tier,
                "enterprise_scale": enterprise_scale,
                "sector": sector,
                "broadened": broadened,
            },
            mode=mode,  # type: ignore[arg-type]
        )
        return res if isinstance(res, list) else [res]

    return get_default_retriever().search_policies(
        query=query,
        state=state,
        district_tier=district_tier,
        enterprise_scale=enterprise_scale,
        sector=sector,
        broadened=broadened,
    )


def get_policy_clause(policy_id: str, clause_id: str) -> dict[str, Any]:
    """Fetch exact numbered clause text, official URL, collection date, and validity date."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "msme-policies-kb",
            "get_policy_clause",
            {"policy_id": policy_id, "clause_id": clause_id},
            mode=mode,  # type: ignore[arg-type]
        )

    res = get_default_retriever().get_policy_clause(policy_id=policy_id, clause_id=clause_id)
    if res is None:
        return {
            "ok": False,
            "policy_id": policy_id,
            "clause_id": clause_id,
            "error": {"code": "CLAUSE_NOT_FOUND", "message": f"Unknown policy_id '{policy_id}'."},
        }
    return {"ok": True, **res}


def get_document_checklist(policy_id: str) -> dict[str, Any]:
    """Return mandatory compliance documents for `policy_id`."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "msme-policies-kb",
            "get_document_checklist",
            {"policy_id": policy_id},
            mode=mode,  # type: ignore[arg-type]
        )

    res = get_default_retriever().get_document_checklist(policy_id=policy_id)
    if res is None:
        return {
            "ok": False,
            "policy_id": policy_id,
            "required_documents": ["udyam_certificate", "gstin_certificate"],
            "error": {"code": "POLICY_NOT_FOUND", "message": f"Unknown policy_id '{policy_id}'."},
        }
    return {"ok": True, **res}


# ---------------------------------------------------------------------------
# 2. MCP Server: `rule-engine` [READ — deterministic Python code]
# ---------------------------------------------------------------------------

def evaluate_eligibility(profile: dict[str, Any], policy_id: str) -> dict[str, Any]:
    """Deterministically evaluate `EnterpriseProfile` against reviewed JSON scheme rules."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "rule-engine",
            "evaluate_eligibility",
            {"profile": profile, "policy_id": policy_id},
            mode=mode,  # type: ignore[arg-type]
        )

    return get_default_rule_engine().evaluate(profile=profile, policy_id=policy_id)


def get_policy_rules(policy_id: str) -> dict[str, Any]:
    """Return the reviewed JSON rule conditions and subsidy formula for `policy_id`."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "rule-engine",
            "get_policy_rules",
            {"policy_id": policy_id},
            mode=mode,  # type: ignore[arg-type]
        )

    spec = get_default_rule_engine().get_policy_rules(policy_id=policy_id)
    if spec is None:
        return {"ok": False, "policy_id": policy_id, "error": {"code": "POLICY_NOT_FOUND", "message": "Unknown policy"}}
    return {"ok": True, **spec}


# ---------------------------------------------------------------------------
# 3. MCP Server: `doc-check` [READ]
# ---------------------------------------------------------------------------

def extract_document_fields(document_id: str, document_type: str, raw_text: str) -> dict[str, Any]:
    """Extract structured enterprise, Udyam, GSTIN, investment, and date fields from synthetic text."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "doc-check",
            "extract_document_fields",
            {"document_id": document_id, "document_type": document_type, "raw_text": raw_text},
            mode=mode,  # type: ignore[arg-type]
        )

    return _extract_docs_impl(document_id=document_id, document_type=document_type, raw_text=raw_text)


def compare_document_fields(
    profile: dict[str, Any],
    uploaded_docs: list[dict[str, Any]],
    required_docs: list[str],
) -> dict[str, Any]:
    """Verify uploaded MSME documents against required checklist and detect cross-document mismatches."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "doc-check",
            "compare_document_fields",
            {"profile": profile, "uploaded_docs": uploaded_docs, "required_docs": required_docs},
            mode=mode,  # type: ignore[arg-type]
        )

    return _compare_docs_impl(profile=profile, uploaded_docs=uploaded_docs, required_docs=required_docs)


# ---------------------------------------------------------------------------
# 4. MCP Server: `mock-portal` [draft/status=READ, submit_application=WRITE]
# ---------------------------------------------------------------------------

def create_application_draft(
    enterprise_id: str,
    policy_id: str,
    profile: dict[str, Any],
    revision: int = 1,
) -> dict[str, Any]:
    """Create a non-binding application draft in SQLite Portal DB with SHA-256 payload hash."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "mock-portal",
            "create_application_draft",
            {
                "enterprise_id": enterprise_id,
                "policy_id": policy_id,
                "profile": profile,
                "revision": revision,
            },
            mode=mode,  # type: ignore[arg-type]
        )

    return get_default_portal_db().create_draft(
        enterprise_id=enterprise_id,
        policy_id=policy_id,
        profile=profile,
        revision=revision,
    )


def submit_application(
    draft_id: str,
    payload: dict[str, Any],
    approval_token: str | None = None,
) -> dict[str, Any]:
    """[WRITE — behind approval gate only] Submit approved draft to SQLite Portal DB."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "mock-portal",
            "submit_application",
            {"draft_id": draft_id, "payload": payload, "approval_token": approval_token},
            mode=mode,  # type: ignore[arg-type]
        )

    return get_default_portal_db().submit(
        draft_id=draft_id,
        payload=payload,
        approval_token=approval_token,
    )


def get_application_status(tracking_id: str) -> dict[str, Any]:
    """Query submitted application status from SQLite Portal DB."""
    mode = _wire_mode()
    if mode:
        return call_mcp_tool_sync(
            "mock-portal",
            "get_application_status",
            {"tracking_id": tracking_id},
            mode=mode,  # type: ignore[arg-type]
        )

    return get_default_portal_db().get_status(tracking_id=tracking_id)
