"""Eligibility Analyst: searches policies and runs deterministic rule-engine code."""

from __future__ import annotations

from typing import Any

from msme_saathi.state import EnterpriseProfile, MSMEState
from msme_saathi.tools import evaluate_eligibility, search_policies


def eligibility_analyst_node(state: MSMEState) -> dict[str, Any]:
    """Query `msme-policies-kb` and evaluate every candidate via `rule-engine`."""
    profile = state.get("profile") or EnterpriseProfile()
    profile_dict = profile.model_dump()

    candidates = search_policies(
        query=state.get("user_query", ""),
        state=profile.state,
        district_tier=profile.taluka_zone,
        enterprise_scale=profile.enterprise_scale,
        sector=profile.sector,
        broadened=state.get("search_broadened", False),
    )

    verdicts = {
        scheme["policy_id"]: evaluate_eligibility(profile=profile_dict, policy_id=scheme["policy_id"])
        for scheme in candidates
    }

    return {
        "eligibility_checked": True,
        "candidate_schemes": candidates,
        "verdicts": verdicts,
    }
