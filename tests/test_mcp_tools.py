"""Comprehensive tests for all 10 tools across the 4 FastMCP servers, permissions, FastAPI portal, and stdio MCP."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from msme_saathi.mcp_servers import (
    MockPortalDB,
    TOOL_PERMISSION_CATALOG,
    create_portal_app,
    make_approval_token,
)
from msme_saathi.tools import (
    allowed_tool_names_for_agent,
    call_mcp_tool_sync,
    check_tool_permission,
    compare_document_fields,
    create_application_draft,
    evaluate_eligibility,
    extract_document_fields,
    get_application_status,
    get_document_checklist,
    get_policy_clause,
    get_policy_rules,
    load_agent_mcp_tools,
    reset_portal_state,
    search_policies,
    submit_application,
)


# ---------------------------------------------------------------------------
# 1. Server `msme-policies-kb` tool tests
# ---------------------------------------------------------------------------

def test_tool_search_policies_multilingual():
    mr_hits = search_policies(
        query="अकोला मध्ये ३५ लाख प्रिसिजन इंजिनिअरिंग मशिनरी अनुदान",
        state="Maharashtra",
        district_tier="D+",
        enterprise_scale="micro",
    )
    assert any(h["policy_id"] == "MH-PSI-2019-CAP" for h in mr_hits)
    assert all(h.get("clause_id") and h.get("collection_date") for h in mr_hits)


def test_tool_get_policy_clause_and_structured_error():
    clause = get_policy_clause("MH-PSI-2019-CAP", "MH-PSI-2019/Ch.4/Cl.4.2")
    assert clause["ok"] is True
    assert "30%" in clause["text"]
    assert clause["validity_date"] == "2027-03-31"

    missing = get_policy_clause("NON-EXISTENT-POLICY", "Cl.1")
    assert missing["ok"] is False
    assert missing["error"]["code"] == "CLAUSE_NOT_FOUND"


def test_tool_get_document_checklist_and_structured_error():
    chk = get_document_checklist("MH-PSI-2019-CAP")
    assert chk["ok"] is True
    assert "udyam_certificate" in chk["required_documents"]
    assert "ca_investment_cert" in chk["required_documents"]

    bad = get_document_checklist("UNKNOWN-SCHEME-999")
    assert bad["ok"] is False
    assert bad["error"]["code"] == "POLICY_NOT_FOUND"


# ---------------------------------------------------------------------------
# 2. Server `rule-engine` tool tests (deterministic boundaries & rupee math)
# ---------------------------------------------------------------------------

def test_tool_evaluate_eligibility_boundaries_and_cant_tell():
    # Missing core fields -> `cant_tell`
    partial = evaluate_eligibility({"state": "Maharashtra", "plant_machinery_investment_lakhs": 50.0}, "MH-PSI-2019-CAP")
    assert partial["verdict"] == "cant_tell"
    assert "udyam_registered" in partial["missing_fields"]

    # Exact boundary: 100.0 Lakhs -> `eligible` (30% = Rs. 30,00,000 cap)
    at_cap = evaluate_eligibility(
        {
            "state": "Maharashtra",
            "taluka_zone": "D+",
            "enterprise_scale": "small",
            "plant_machinery_investment_lakhs": 100.0,
            "udyam_registered": True,
            "commercial_production_date": "2025-01-10",
        },
        "MH-PSI-2019-CAP",
    )
    assert at_cap["verdict"] == "eligible"
    assert at_cap["estimated_subsidy_inr"] == 3_000_000.0
    assert at_cap["deciding_clause"] == "MH-PSI-2019/Ch.4/Cl.4.2"

    # Just over boundary: 100.1 Lakhs -> `not_eligible`
    over_cap = evaluate_eligibility(
        {
            "state": "Maharashtra",
            "taluka_zone": "D+",
            "enterprise_scale": "small",
            "plant_machinery_investment_lakhs": 100.1,
            "udyam_registered": True,
            "commercial_production_date": "2025-01-10",
        },
        "MH-PSI-2019-CAP",
    )
    assert over_cap["verdict"] == "not_eligible"
    assert over_cap["estimated_subsidy_inr"] == 0.0

    # Unknown policy -> structured error (never crashes)
    unknown = evaluate_eligibility({"udyam_registered": True}, "DOES-NOT-EXIST")
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == "POLICY_NOT_FOUND"


def test_tool_get_policy_rules():
    rules = get_policy_rules("CENT-CLCSS-TUS")
    assert rules["ok"] is True
    assert rules["human_reviewed"] is True
    assert rules["subsidy_formula"]["rate_pct"] == 15.0


# ---------------------------------------------------------------------------
# 3. Server `doc-check` tool tests
# ---------------------------------------------------------------------------

def test_tool_extract_document_fields_and_pan_masking():
    raw = (
        "Enterprise Name: Sahyadri Precision Tools Pvt Ltd\n"
        "Udyam Reg: UDYAM-MH-02-0012345\n"
        "GSTIN: 27AABCS1429B1Z5\n"
        "PAN: AABCS1429B\n"
        "Machinery Value: Rs. 35.0 Lakhs\n"
        "Date: 2024-06-01"
    )
    out = extract_document_fields("DOC-01", "udyam_certificate", raw)
    assert out["ok"] is True
    assert out["enterprise_name"] == "Sahyadri Precision Tools Pvt Ltd"
    assert out["udyam_number"] == "UDYAM-MH-02-0012345"
    assert out["gst_state"] == "Maharashtra"
    assert out["pan_masked"] == "AA*****9B"
    assert "AABCS1429B" not in str(out["pan_masked"])
    assert out["investment_lakhs"] == 35.0

    err = extract_document_fields("", "", "empty")
    assert err["ok"] is False
    assert err["error"]["code"] == "INVALID_DOCUMENT_INPUT"


def test_tool_compare_document_fields_flags_name_state_and_investment_mismatches():
    report = compare_document_fields(
        profile={"state": "Maharashtra", "plant_machinery_investment_lakhs": 35.0},
        uploaded_docs=[
            {"document_type": "udyam_certificate", "enterprise_name": "Sahyadri Precision Tools Pvt Ltd"},
            {"document_type": "gstin_certificate", "enterprise_name": "Sahyadri Precision Works", "gstin": "24AABCS1429B1Z5"},
            {"document_type": "ca_investment_cert", "investment_lakhs": 52.0},
        ],
        required_docs=["udyam_certificate", "gstin_certificate", "ca_investment_cert", "machinery_invoices"],
    )
    assert report["missing_documents"] == ["machinery_invoices"]
    assert report["requires_human_referral"] is True
    flagged_fields = {m["field"] for m in report["mismatches"]}
    assert {"enterprise_name", "gstin_state_code", "plant_machinery_investment_lakhs"} <= flagged_fields


# ---------------------------------------------------------------------------
# 4. Server `mock-portal` tool tests + SHA-256 payload tamper invalidation + FastAPI
# ---------------------------------------------------------------------------

def test_tools_mock_portal_draft_submit_tamper_and_status():
    reset_portal_state()
    draft = create_application_draft(
        enterprise_id="ENT-PORTAL-01",
        policy_id="MH-PSI-2019-CAP",
        profile={"enterprise_name": "Kaveri Auto Parts", "state": "Maharashtra", "plant_machinery_investment_lakhs": 40.0},
    )
    assert draft["ok"] is True
    assert draft["payload_sha256"]

    # 1. Missing approval token -> blocked
    no_tok = submit_application(draft["draft_id"], draft["payload"], approval_token=None)
    assert no_tok["ok"] is False
    assert no_tok["status"] == "rejected_missing_approval"

    # 2. Subsequent edit to payload invalidates earlier approval!
    token = make_approval_token(draft["draft_id"], draft["payload"])
    tampered_payload = {**draft["payload"], "plant_machinery_investment_lakhs": 95.0}
    tampered = submit_application(draft["draft_id"], tampered_payload, approval_token=token)
    assert tampered["ok"] is False
    assert tampered["status"] == "rejected_payload_tampered"

    # 3. Exact payload + valid hash-bound token -> succeeds
    ok_sub = submit_application(draft["draft_id"], draft["payload"], approval_token=token)
    assert ok_sub["ok"] is True
    status = get_application_status(ok_sub["tracking_id"])
    assert status["ok"] is True
    assert status["status"] == "submitted"

    missing_status = get_application_status("TRK-NONEXISTENT")
    assert missing_status["ok"] is False
    assert missing_status["status"] == "not_found"


def test_mock_portal_fastapi_endpoints(tmp_path: Path):
    db = MockPortalDB(db_path=tmp_path / "portal_api.sqlite")
    client = TestClient(create_portal_app(db=db))

    assert client.get("/health").json()["status"] == "ok"
    d_resp = client.post(
        "/drafts",
        json={
            "enterprise_id": "ENT-API-1",
            "policy_id": "CENT-CLCSS-TUS",
            "profile": {"enterprise_name": "Deccan Gears", "state": "Maharashtra", "plant_machinery_investment_lakhs": 25.0},
        },
    ).json()
    assert d_resp["ok"] is True

    token = make_approval_token(d_resp["draft_id"], d_resp["payload"])
    s_resp = client.post(
        "/submissions",
        json={"draft_id": d_resp["draft_id"], "payload": d_resp["payload"], "approval_token": token},
    ).json()
    assert s_resp["ok"] is True
    st_resp = client.get(f"/status/{s_resp['tracking_id']}").json()
    assert st_resp["status"] == "submitted"


# ---------------------------------------------------------------------------
# 5. READ/WRITE permission catalog & live FastMCP `stdio` wire roundtrip
# ---------------------------------------------------------------------------

def test_every_tool_marked_read_or_write_and_write_isolated_to_application_submit():
    write_tools = [name for name, spec in TOOL_PERMISSION_CATALOG.items() if spec["access"] == "write"]
    assert write_tools == ["submit_application"]

    for agent in ("intake_agent", "eligibility_analyst", "document_checker", "application_draft", "response_synthesizer"):
        perm = check_tool_permission(agent, "submit_application")
        assert perm["allowed"] is False
        assert "submit_application" not in allowed_tool_names_for_agent(agent)

    assert check_tool_permission("application_submit", "submit_application")["allowed"] is True


def test_live_fastmcp_stdio_roundtrip_via_langchain_mcp_adapters():
    """Spawn actual FastMCP servers over stdio via `langchain-mcp-adapters` and verify tool execution."""
    tools = asyncio.run(load_agent_mcp_tools("eligibility_analyst", mode="stdio"))
    tool_names = {t.name for t in tools}
    assert {"search_policies", "get_policy_clause", "evaluate_eligibility", "get_policy_rules"} == tool_names
    assert "submit_application" not in tool_names

    verdict = call_mcp_tool_sync(
        "rule-engine",
        "evaluate_eligibility",
        {
            "profile": {
                "state": "Maharashtra",
                "taluka_zone": "D+",
                "enterprise_scale": "micro",
                "plant_machinery_investment_lakhs": 35.0,
                "udyam_registered": True,
                "commercial_production_date": "2024-06-01",
            },
            "policy_id": "MH-PSI-2019-CAP",
        },
        mode="stdio",
    )
    assert verdict["verdict"] == "eligible"
    assert verdict["estimated_subsidy_inr"] == 1_050_000.0
