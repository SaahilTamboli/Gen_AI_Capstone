"""Application Agent nodes: Draft -> Approval Gate (interrupt) -> Submit -> Final Reply."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from msme_saathi.state import EnterpriseProfile, MSMEState
from msme_saathi.tools import create_application_draft, submit_application


def application_draft_node(state: MSMEState) -> dict[str, Any]:
    """Prepare the subsidy draft payload via `mock-portal.create_application_draft`."""
    profile = state.get("profile") or EnterpriseProfile()
    policy_id = state.get("selected_policy_id") or "UNKNOWN"
    prev = state.get("draft_application")
    revision = (prev["revision"] + 1) if prev else 1

    draft = create_application_draft(
        enterprise_id=profile.enterprise_id,
        policy_id=policy_id,
        profile=profile.model_dump(),
        revision=revision,
    )
    return {"draft_application": draft, "approval_decision": None}


def approval_gate_node(state: MSMEState) -> dict[str, Any]:
    """Human-in-the-loop `interrupt()` stub sitting directly before `mock-portal.submit_application`.

    Integration point for Team Member 4:
    Plug in exact payload SHA-256 hashing, approval-token signing, and FastAPI/UI resume logic here.
    """
    draft = state.get("draft_application")
    decision = interrupt(
        {
            "type": "approval_gate",
            "action": "mock-portal.submit_application",
            "draft": draft,
            "document_report": state.get("document_report"),
            "options": ["approve", "edit", "reject"],
        }
    )

    if isinstance(decision, str):
        decision = {"decision": decision}

    action = decision.get("decision", "reject")
    if action == "approve":
        token = decision.get("approval_token") or f"APPROVED-{draft['draft_id']}"
        return {"approval_decision": {"decision": "approve", "approval_token": token}}

    if action == "edit":
        profile = (state.get("profile") or EnterpriseProfile()).model_copy(deep=True)
        edits = {k: v for k, v in (decision.get("edits") or {}).items() if hasattr(profile, k)}
        return {
            "profile": profile.model_copy(update=edits),
            "approval_decision": {"decision": "edit", "edits": edits},
            "replan_reason": "human_edit",
        }

    return {"approval_decision": {"decision": "reject", "reason": decision.get("reason", "Rejected by reviewer")}}


def application_submit_node(state: MSMEState) -> dict[str, Any]:
    """Submit the approved draft via `mock-portal.submit_application` (write access isolated here)."""
    draft = state.get("draft_application")
    approval = state.get("approval_decision") or {}
    result = submit_application(
        draft_id=draft["draft_id"],
        payload=draft["payload"],
        approval_token=approval.get("approval_token"),
    )
    return {"submission_result": result}


def response_synthesizer_node(state: MSMEState) -> dict[str, Any]:
    """Compile a concise, clause-cited final summary."""
    plan = state.get("plan")
    verdicts = state.get("verdicts", {})
    doc_report = state.get("document_report")
    submission = state.get("submission_result")

    lines = [f"Plan v{plan.version} (replans: {len(plan.replans)})" if plan else "Plan: N/A"]
    for v in verdicts.values():
        lines.append(f"- {v['policy_id']}: {v['verdict'].upper()} [Clause: {v['deciding_clause']}]")
    if doc_report and doc_report.get("mismatches"):
        lines.append(f"- Document Mismatches: {len(doc_report['mismatches'])} (DIC review required)")
    if submission and submission.get("ok"):
        lines.append(f"- Submitted: {submission['tracking_id']}")
    else:
        lines.append("- Submission: Not submitted")

    reply = "\n".join(lines)
    return {"final_response": reply, "messages": [AIMessage(content=reply)]}
