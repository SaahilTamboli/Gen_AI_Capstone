"""Lightweight local Python stubs mirroring the 4 agreed FastMCP server contracts.

Integration point for Team Member 2 (Knowledge, Memory & Tools Engineer):
Replace these stub functions with `langchain-mcp-adapters` tool calls to:
  1. `msme-policies-kb`  (read)  -> search_policies, get_policy_clause, get_document_checklist
  2. `rule-engine`       (read)  -> evaluate_eligibility
  3. `doc-check`         (read)  -> extract_document_fields, compare_document_fields
  4. `mock-portal`       (write) -> create_application_draft, submit_application, get_application_status
"""

from __future__ import annotations

from typing import Any

# In-memory stub store for mock-portal tests
_PORTAL_SUBMISSIONS: dict[str, dict[str, Any]] = {}


def reset_portal_state() -> None:
    """Clear mock portal submissions between test runs."""
    _PORTAL_SUBMISSIONS.clear()


# ---------------------------------------------------------------------------
# 1. MCP Server Stub: `msme-policies-kb` [READ]
# ---------------------------------------------------------------------------

def search_policies(
    query: str,
    state: str | None = None,
    district_tier: str | None = None,
    enterprise_scale: str | None = None,
    sector: str | None = None,
    broadened: bool = False,
) -> list[dict[str, Any]]:
    """Stub for `msme-policies-kb.search_policies`."""
    all_policies = [
        {
            "policy_id": "MH-PSI-2019-CAP",
            "scheme_name": "Maharashtra PSI-2019 Capital Subsidy (Group D/D+)",
            "state": "Maharashtra",
            "clause_id": "MH-PSI-2019/Ch.4/Cl.4.2",
            "validity_date": "2027-03-31",
            "snippet": "30% capital subsidy (up to Rs. 30L) for Udyam Micro/Small units <= Rs. 100L in Zone D/D+.",
        },
        {
            "policy_id": "MH-GREEN-SOLAR-2024",
            "scheme_name": "Maharashtra MSME Rooftop Solar & Green Tech Grant",
            "state": "Maharashtra",
            "clause_id": "MH-GREEN-2024/Sec.3/Cl.3.4",
            "validity_date": "2028-03-31",
            "snippet": "25% capital subsidy (up to Rs. 15L) for solar & green machinery upgrades.",
        },
        {
            "policy_id": "CENT-CLCSS-TUS",
            "scheme_name": "Central CLCSS Technology Upgradation Subsidy",
            "state": "ALL",
            "clause_id": "CLCSS-TUS/Para.5.1",
            "validity_date": "2027-03-31",
            "snippet": "15% upfront capital subsidy on tooling/machinery upgrades up to Rs. 100L.",
        },
    ]

    if broadened or not state:
        return all_policies

    # Targeted search: return state-matching schemes first; central schemes come in on broaden replan
    state_matches = [p for p in all_policies if p["state"].lower() == state.lower()]
    return state_matches


def get_policy_clause(policy_id: str, clause_id: str) -> dict[str, Any]:
    """Stub for `msme-policies-kb.get_policy_clause`."""
    return {
        "policy_id": policy_id,
        "clause_id": clause_id,
        "text": f"Official policy clause {clause_id} for {policy_id} (stub text).",
        "validity_date": "2027-03-31",
    }


def get_document_checklist(policy_id: str) -> dict[str, Any]:
    """Stub for `msme-policies-kb.get_document_checklist`."""
    checklists = {
        "MH-PSI-2019-CAP": [
            "udyam_certificate",
            "gstin_certificate",
            "ca_investment_cert",
            "machinery_invoices",
            "electricity_sanction_letter",
        ],
        "MH-GREEN-SOLAR-2024": [
            "udyam_certificate",
            "gstin_certificate",
            "solar_vendor_quotation",
        ],
        "CENT-CLCSS-TUS": [
            "udyam_certificate",
            "ca_investment_cert",
            "machinery_invoices",
        ],
    }
    return {
        "policy_id": policy_id,
        "required_documents": checklists.get(policy_id, ["udyam_certificate", "gstin_certificate"]),
    }


# ---------------------------------------------------------------------------
# 2. MCP Server Stub: `rule-engine` [READ — deterministic Python code]
# ---------------------------------------------------------------------------

def evaluate_eligibility(profile: dict[str, Any], policy_id: str) -> dict[str, Any]:
    """Stub for `rule-engine.evaluate_eligibility`.

    Returns `eligible`, `not_eligible`, or `cant_tell` with `deciding_clause` and `missing_fields`.
    """
    missing = [
        f for f in ("udyam_registered", "enterprise_scale", "commercial_production_date")
        if profile.get(f) is None
    ]
    if missing:
        return {
            "policy_id": policy_id,
            "verdict": "cant_tell",
            "deciding_clause": f"{policy_id}/Eligibility",
            "missing_fields": missing,
            "estimated_subsidy_inr": None,
            "reason": f"Missing required fields: {', '.join(missing)}",
        }

    investment = profile.get("plant_machinery_investment_lakhs") or 0.0
    udyam = bool(profile.get("udyam_registered"))
    scale = profile.get("enterprise_scale")

    if not udyam or scale not in {"micro", "small"}:
        return {
            "policy_id": policy_id,
            "verdict": "not_eligible",
            "deciding_clause": f"{policy_id}/Cl.3.1",
            "missing_fields": [],
            "estimated_subsidy_inr": 0,
            "reason": "Requires Udyam-registered Micro or Small enterprise.",
        }

    if policy_id == "MH-PSI-2019-CAP":
        if profile.get("state") == "Maharashtra" and investment <= 100.0:
            return {
                "policy_id": policy_id,
                "verdict": "eligible",
                "deciding_clause": "MH-PSI-2019/Ch.4/Cl.4.2",
                "missing_fields": [],
                "estimated_subsidy_inr": min(investment * 100_000 * 0.30, 3_000_000),
                "reason": "Qualifies for 30% capital subsidy under Group D/D+ taluka cap (<= Rs. 100L).",
            }
        return {
            "policy_id": policy_id,
            "verdict": "not_eligible",
            "deciding_clause": "MH-PSI-2019/Ch.4/Cl.4.2(b)",
            "missing_fields": [],
            "estimated_subsidy_inr": 0,
            "reason": "Outside Maharashtra or plant & machinery investment exceeds Rs. 100L cap.",
        }

    if policy_id == "MH-GREEN-SOLAR-2024":
        if profile.get("state") == "Maharashtra" and profile.get("solar_upgrade_requested"):
            return {
                "policy_id": policy_id,
                "verdict": "eligible",
                "deciding_clause": "MH-GREEN-2024/Sec.3/Cl.3.4",
                "missing_fields": [],
                "estimated_subsidy_inr": min(investment * 100_000 * 0.25, 1_500_000),
                "reason": "Qualifies for 25% Maharashtra green/solar equipment capital grant.",
            }
        return {
            "policy_id": policy_id,
            "verdict": "not_eligible",
            "deciding_clause": "MH-GREEN-2024/Sec.3/Cl.3.4",
            "missing_fields": [],
            "estimated_subsidy_inr": 0,
            "reason": "Requires Maharashtra unit adopting solar/green technology.",
        }

    # CENT-CLCSS-TUS fallback
    if investment <= 100.0:
        return {
            "policy_id": policy_id,
            "verdict": "eligible",
            "deciding_clause": "CLCSS-TUS/Para.5.1",
            "missing_fields": [],
            "estimated_subsidy_inr": min(investment * 100_000 * 0.15, 1_500_000),
            "reason": "Qualifies for 15% central capital subsidy for technology/tooling upgradation.",
        }
    return {
        "policy_id": policy_id,
        "verdict": "not_eligible",
        "deciding_clause": "CLCSS-TUS/Para.5.1",
        "missing_fields": [],
        "estimated_subsidy_inr": 0,
        "reason": "Investment exceeds Rs. 100L ceiling for CLCSS.",
    }


# ---------------------------------------------------------------------------
# 3. MCP Server Stub: `doc-check` [READ]
# ---------------------------------------------------------------------------

def extract_document_fields(document_id: str, document_type: str, raw_text: str) -> dict[str, Any]:
    """Stub for `doc-check.extract_document_fields`."""
    return {"document_id": document_id, "document_type": document_type, "raw_text": raw_text}


def compare_document_fields(
    profile: dict[str, Any],
    uploaded_docs: list[dict[str, Any]],
    required_docs: list[str],
) -> dict[str, Any]:
    """Stub for `doc-check.compare_document_fields`."""
    present = {d.get("document_type") for d in uploaded_docs}
    missing = [doc for doc in required_docs if doc not in present]

    # Simple realistic mismatch stub: flag if GSTIN name differs from Udyam name
    names = {d.get("document_type"): d.get("enterprise_name") for d in uploaded_docs if d.get("enterprise_name")}
    mismatches = []
    if "udyam_certificate" in names and "gstin_certificate" in names:
        if names["udyam_certificate"].lower() != names["gstin_certificate"].lower():
            mismatches.append(
                {
                    "field": "enterprise_name",
                    "udyam_value": names["udyam_certificate"],
                    "gstin_value": names["gstin_certificate"],
                    "fix": "Amend Udyam or file GST REG-14 so enterprise names match identically.",
                }
            )

    return {
        "present_documents": sorted(p for p in present if p),
        "missing_documents": missing,
        "mismatches": mismatches,
        "requires_human_referral": bool(mismatches),
    }


# ---------------------------------------------------------------------------
# 4. MCP Server Stub: `mock-portal` [draft/status=READ, submit=WRITE]
# ---------------------------------------------------------------------------

def create_application_draft(
    enterprise_id: str,
    policy_id: str,
    profile: dict[str, Any],
    revision: int = 1,
) -> dict[str, Any]:
    """Stub for `mock-portal.create_application_draft`."""
    draft_id = f"DRAFT-{policy_id}-{enterprise_id}-R{revision}"
    return {
        "draft_id": draft_id,
        "policy_id": policy_id,
        "revision": revision,
        "payload": {
            "enterprise_id": enterprise_id,
            "enterprise_name": profile.get("enterprise_name") or "Unnamed MSME Unit",
            "state": profile.get("state"),
            "district": profile.get("district"),
            "enterprise_scale": profile.get("enterprise_scale"),
            "plant_machinery_investment_lakhs": profile.get("plant_machinery_investment_lakhs"),
            "policy_id": policy_id,
        },
    }


def submit_application(
    draft_id: str,
    payload: dict[str, Any],
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Stub for `mock-portal.submit_application` [WRITE — behind approval gate only].

    Team Member 4 will plug in exact payload-hash & signed token verification here.
    """
    if not approval_token:
        return {"ok": False, "draft_id": draft_id, "status": "rejected_missing_approval"}

    tracking_id = f"TRK-{draft_id}"
    record = {
        "ok": True,
        "tracking_id": tracking_id,
        "draft_id": draft_id,
        "policy_id": payload.get("policy_id"),
        "status": "submitted",
    }
    _PORTAL_SUBMISSIONS[tracking_id] = record
    return record


def get_application_status(tracking_id: str) -> dict[str, Any]:
    """Stub for `mock-portal.get_application_status`."""
    return _PORTAL_SUBMISSIONS.get(tracking_id, {"ok": False, "tracking_id": tracking_id, "status": "not_found"})
