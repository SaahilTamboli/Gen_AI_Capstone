"""Supervisor + Planner node: maintains visible ExecutionPlan, routes specialists, and replans."""

from __future__ import annotations

from collections import Counter
from typing import Any

from msme_saathi.state import ExecutionPlan, MSMEState, PlanStage, ReplanRecord


def _replan(plan: ExecutionPlan, trigger: str, reason: str, stages: list[PlanStage]) -> ExecutionPlan:
    updated = plan.model_copy(deep=True)
    updated.version += 1
    for stage in stages:
        updated.set_status(stage, "needs_replan")
    updated.replans.append(ReplanRecord(version=updated.version, trigger=trigger, reason=reason))
    return updated


def supervisor_planner_node(state: MSMEState) -> dict[str, Any]:
    """Decide the next specialist and handle replanning branches."""
    plan = (state.get("plan") or ExecutionPlan()).model_copy(deep=True)
    verdicts = state.get("verdicts", {})
    intake_turns = state.get("intake_turns", 0)
    replan_reason = state.get("replan_reason")

    # Replan Branch 1: Human reviewer chose `edit` at the approval gate
    if replan_reason == "human_edit":
        plan = _replan(
            plan,
            trigger="approval_gate_edit",
            reason="Human edited application details; re-evaluating rules and regenerating draft.",
            stages=["rules", "documents", "draft"],
        )
        return {
            "plan": plan,
            "replan_reason": None,
            "eligibility_checked": False,
            "verdicts": {},
            "document_report": None,
            "draft_application": None,
            "approval_decision": None,
            "next_node": "eligibility_analyst",
        }

    # Step 1: Initial profile intake
    if not state.get("intake_done", False):
        plan.set_status("profile", "in_progress")
        return {"plan": plan, "next_node": "intake_agent"}

    # Step 2 & 3: Search candidate policies & run rule engine
    if not state.get("eligibility_checked", False):
        plan.set_status("profile", "completed")
        plan.set_status("candidate_schemes", "in_progress")
        plan.set_status("rules", "in_progress")
        return {"plan": plan, "next_node": "eligibility_analyst"}

    eligible = [v for v in verdicts.values() if v["verdict"] == "eligible"]
    cant_tell = [v for v in verdicts.values() if v["verdict"] == "cant_tell"]

    # Replan Branch 2: `cant_tell` verdicts -> ask minimal targeted question for highest-gain fields
    if cant_tell and intake_turns < 3:
        counts = Counter(f for v in cant_tell for f in v.get("missing_fields", []))
        top_fields = [field for field, _ in counts.most_common(2)]
        if top_fields:
            plan = _replan(
                plan,
                trigger="cant_tell_missing_fields",
                reason=f"Missing {top_fields} for {len(cant_tell)} scheme(s); routing back to Intake.",
                stages=["profile", "rules"],
            )
            plan.set_status("profile", "in_progress")
            return {
                "plan": plan,
                "pending_missing_fields": top_fields,
                "next_node": "intake_agent",
            }

    # Replan Branch 3: Zero eligible schemes under state filter -> broaden to central schemes
    if not eligible and not state.get("search_broadened", False):
        plan = _replan(
            plan,
            trigger="zero_eligible_broaden_search",
            reason="No state schemes qualified; broadening search to central MSME schemes.",
            stages=["candidate_schemes", "rules"],
        )
        plan.set_status("candidate_schemes", "in_progress")
        return {
            "plan": plan,
            "search_broadened": True,
            "eligibility_checked": False,
            "verdicts": {},
            "next_node": "eligibility_analyst",
        }

    if not eligible:
        plan.set_status("candidate_schemes", "completed")
        plan.set_status("rules", "completed")
        plan.set_status("documents", "skipped")
        plan.set_status("draft", "skipped")
        return {"plan": plan, "next_node": "response_synthesizer"}

    plan.set_status("profile", "completed")
    plan.set_status("candidate_schemes", "completed")
    plan.set_status("rules", "completed")

    # Step 4: Document check
    if state.get("document_report") is None:
        plan.set_status("documents", "in_progress")
        return {"plan": plan, "next_node": "document_checker"}
    plan.set_status("documents", "completed")

    # Step 5: Draft -> Approval Gate (interrupt) -> Submit
    if state.get("draft_application") is None:
        best = max(eligible, key=lambda v: v.get("estimated_subsidy_inr") or 0)
        plan.set_status("draft", "in_progress")
        return {"plan": plan, "selected_policy_id": best["policy_id"], "next_node": "application_draft"}

    approval = state.get("approval_decision")
    if approval is None:
        return {"plan": plan, "next_node": "approval_gate"}

    if approval.get("decision") == "reject":
        plan.set_status("draft", "skipped")
        return {"plan": plan, "next_node": "response_synthesizer"}

    if approval.get("decision") == "approve" and state.get("submission_result") is None:
        return {"plan": plan, "next_node": "application_submit"}

    plan.set_status("draft", "completed")
    return {"plan": plan, "next_node": "response_synthesizer"}
