"""Intake Agent: builds typed EnterpriseProfile and asks minimal follow-up questions."""

from __future__ import annotations

import re
from typing import Any

from langgraph.types import interrupt

from msme_saathi.state import EnterpriseProfile, MSMEState


def _extract_basic_fields(text: str, profile: EnterpriseProfile) -> EnterpriseProfile:
    """Lightweight Day 2–3 extractor (to be replaced with an LLM structured extractor in Day 4+)."""
    lower = text.lower()
    updates: dict[str, Any] = {}

    if not profile.district and ("akola" in lower or "अकोला" in text):
        updates.update({"district": "Akola", "state": "Maharashtra", "taluka_zone": "D+"})
    if not profile.sector and any(w in lower for w in ("precision", "engineering", "tooling")):
        updates["sector"] = "precision_engineering"
    if profile.plant_machinery_investment_lakhs is None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakhs?|लाख)", lower)
        if match:
            updates["plant_machinery_investment_lakhs"] = float(match.group(1))
    if profile.solar_upgrade_requested is None and ("solar" in lower or "सोलर" in text):
        updates["solar_upgrade_requested"] = True

    return profile.model_copy(update=updates)


def intake_agent_node(state: MSMEState) -> dict[str, Any]:
    """Parse initial profile or pause via interrupt() to collect missing eligibility fields."""
    profile = state.get("profile") or EnterpriseProfile()
    missing_fields = state.get("pending_missing_fields") or []

    # First pass: extract from user_query without interrupting
    if not state.get("intake_done", False) and not missing_fields:
        return {
            "profile": _extract_basic_fields(state.get("user_query", ""), profile),
            "intake_done": True,
        }

    # Replanned pass: ask only for the missing fields that settle open `cant_tell` verdicts
    lang = state.get("language", "en")
    question = (
        f"पात्रतेसाठी कृपया ही माहिती द्या: {', '.join(missing_fields)}?"
        if lang == "mr"
        else f"To settle eligibility, please provide: {', '.join(missing_fields)}?"
    )

    reply = interrupt({"type": "intake_question", "language": lang, "question": question, "fields": missing_fields})

    updated = profile.model_copy(deep=True)
    if isinstance(reply, dict):
        valid = {k: v for k, v in reply.items() if hasattr(updated, k)}
        updated = updated.model_copy(update=valid)
    elif isinstance(reply, str):
        updated = _extract_basic_fields(reply, updated)

    return {
        "profile": updated,
        "intake_turns": state.get("intake_turns", 0) + 1,
        "questions_asked": [*state.get("questions_asked", []), question],
        "pending_missing_fields": [],
        "eligibility_checked": False,
        "verdicts": {},  # Re-run rule engine with updated profile
    }
