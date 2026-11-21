"""Focused pytest suite for the MSME-Incentive Saathi LangGraph skeleton."""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.types import Command

from msme_saathi import EnterpriseProfile, build_graph, export_mermaid, sqlite_checkpointer
from msme_saathi.tools import get_application_status, reset_portal_state


@pytest.fixture(autouse=True)
def _clean_portal():
    reset_portal_state()
    yield
    reset_portal_state()


def _akola_marathi_state() -> dict:
    return {
        "language": "mr",
        "user_query": "अकोला मध्ये प्रिसिजन इंजिनिअरिंग युनिट आहे, ₹35 लाख गुंतवणूक आणि सोलर पॅनेल्स बसवायचे आहेत.",
        "profile": EnterpriseProfile(
            enterprise_id="ENT-AKOLA-01",
            enterprise_name="Sahyadri Precision Tools Pvt Ltd",
            uploaded_documents=[
                {"document_type": "udyam_certificate", "enterprise_name": "Sahyadri Precision Tools Pvt Ltd"},
                {"document_type": "gstin_certificate", "enterprise_name": "Sahyadri Precision Works"},
            ],
        ),
    }


def test_end_to_end_with_replan_and_approval_gate(tmp_path: Path):
    """Intake -> cant_tell replan -> rule-engine -> doc mismatch -> approval_gate interrupt() -> submit."""
    cfg = {"configurable": {"thread_id": "t-e2e"}}
    with sqlite_checkpointer(tmp_path / "e2e.sqlite") as saver:
        graph = build_graph(checkpointer=saver)

        # 1. Missing Udyam/scale/date triggers cant_tell replan -> pauses at intake question
        res1 = graph.invoke(_akola_marathi_state(), config=cfg)
        assert res1["__interrupt__"][0].value["type"] == "intake_question"
        plan = graph.get_state(cfg).values["plan"]
        assert [s.stage for s in plan.steps] == ["profile", "candidate_schemes", "rules", "documents", "draft"]
        assert any(r.trigger == "cant_tell_missing_fields" for r in plan.replans)

        # 2. Answer intake question -> runs rules + doc check -> halts at approval_gate before submit
        res2 = graph.invoke(
            Command(resume={"udyam_registered": True, "enterprise_scale": "micro", "commercial_production_date": "2024-06-01"}),
            config=cfg,
        )
        gate = res2["__interrupt__"][0].value
        assert gate["type"] == "approval_gate"
        assert gate["draft"]["policy_id"] == "MH-PSI-2019-CAP"
        assert len(gate["document_report"]["mismatches"]) == 1

        # 3. Approve -> calls mock-portal.submit_application
        res3 = graph.invoke(Command(resume={"decision": "approve"}), config=cfg)
        assert res3["submission_result"]["ok"] is True
        tid = res3["submission_result"]["tracking_id"]
        assert get_application_status(tid)["status"] == "submitted"


def test_sqlite_mid_run_crash_and_resume(tmp_path: Path):
    """Stop mid-run before document_checker, close SQLite, reopen fresh graph, and resume to completion."""
    db_file = tmp_path / "crash.sqlite"
    cfg = {"configurable": {"thread_id": "t-crash"}}
    complete_state = {
        "language": "en",
        "user_query": "Akola precision engineering unit with 35 lakhs machinery and solar panels.",
        "profile": EnterpriseProfile(
            enterprise_id="ENT-CRASH",
            state="Maharashtra",
            district="Akola",
            taluka_zone="D+",
            enterprise_scale="micro",
            plant_machinery_investment_lakhs=35.0,
            udyam_registered=True,
            commercial_production_date="2024-05-01",
            solar_upgrade_requested=True,
        ),
    }

    # Process 1: stops before document_checker and exits
    with sqlite_checkpointer(db_file) as saver1:
        g1 = build_graph(checkpointer=saver1, interrupt_before=["document_checker"])
        g1.invoke(complete_state, config=cfg)
        assert g1.get_state(cfg).next == ("document_checker",)

    # Process 2: fresh SQLite connection + freshly compiled graph resumes mid-run
    with sqlite_checkpointer(db_file) as saver2:
        g2 = build_graph(checkpointer=saver2)
        assert g2.get_state(cfg).next == ("document_checker",)
        res_gate = g2.invoke(None, config=cfg)
        assert res_gate["__interrupt__"][0].value["type"] == "approval_gate"

        res_done = g2.invoke(Command(resume={"decision": "approve"}), config=cfg)
        assert res_done["submission_result"]["ok"] is True


def test_approval_gate_edit_triggers_replan(tmp_path: Path):
    """Editing investment above Rs. 100L at approval_gate replans and switches eligible policy."""
    cfg = {"configurable": {"thread_id": "t-edit"}}
    state = {
        "language": "en",
        "user_query": "Akola unit with 35 lakhs machinery and solar upgrade.",
        "profile": EnterpriseProfile(
            enterprise_id="ENT-EDIT",
            state="Maharashtra",
            district="Akola",
            taluka_zone="D+",
            enterprise_scale="small",
            plant_machinery_investment_lakhs=35.0,
            udyam_registered=True,
            commercial_production_date="2024-02-01",
            solar_upgrade_requested=True,
        ),
    }

    with sqlite_checkpointer(tmp_path / "edit.sqlite") as saver:
        graph = build_graph(checkpointer=saver)
        res1 = graph.invoke(state, config=cfg)
        assert res1["__interrupt__"][0].value["draft"]["policy_id"] == "MH-PSI-2019-CAP"

        # Human edits investment to 120L (> 100L PSI cap) -> replans & switches to MH-GREEN-SOLAR-2024
        res2 = graph.invoke(
            Command(resume={"decision": "edit", "edits": {"plant_machinery_investment_lakhs": 120.0}}),
            config=cfg,
        )
        gate2 = res2["__interrupt__"][0].value
        assert gate2["type"] == "approval_gate"
        assert gate2["draft"]["policy_id"] == "MH-GREEN-SOLAR-2024"
        assert any(r.trigger == "approval_gate_edit" for r in graph.get_state(cfg).values["plan"].replans)


def test_approval_gate_reject_skips_submission(tmp_path: Path):
    """Rejecting at approval_gate never calls submit_application."""
    cfg = {"configurable": {"thread_id": "t-reject"}}
    state = {
        "language": "en",
        "user_query": "Akola unit with 35 lakhs investment.",
        "profile": EnterpriseProfile(
            enterprise_id="ENT-REJ",
            state="Maharashtra",
            district="Akola",
            taluka_zone="D+",
            enterprise_scale="small",
            plant_machinery_investment_lakhs=35.0,
            udyam_registered=True,
            commercial_production_date="2024-02-01",
            solar_upgrade_requested=False,
        ),
    }

    with sqlite_checkpointer(tmp_path / "reject.sqlite") as saver:
        graph = build_graph(checkpointer=saver)
        graph.invoke(state, config=cfg)
        res = graph.invoke(Command(resume={"decision": "reject"}), config=cfg)
        assert res.get("submission_result") is None
        assert "Not submitted" in res["final_response"]


def test_replan_broadens_search_for_non_maharashtra_state(tmp_path: Path):
    """When state search finds no schemes, supervisor replans and broadens to central CLCSS."""
    cfg = {"configurable": {"thread_id": "t-broaden"}}
    state = {
        "language": "en",
        "user_query": "Small engineering unit in Gujarat with 40 lakhs machinery.",
        "profile": EnterpriseProfile(
            enterprise_id="ENT-GUJ",
            state="Gujarat",
            district="Surat",
            enterprise_scale="small",
            plant_machinery_investment_lakhs=40.0,
            udyam_registered=True,
            commercial_production_date="2024-01-15",
            solar_upgrade_requested=False,
        ),
    }

    with sqlite_checkpointer(tmp_path / "broaden.sqlite") as saver:
        graph = build_graph(checkpointer=saver)
        res = graph.invoke(state, config=cfg)
        gate = res["__interrupt__"][0].value
        assert gate["draft"]["policy_id"] == "CENT-CLCSS-TUS"
        assert any(r.trigger == "zero_eligible_broaden_search" for r in graph.get_state(cfg).values["plan"].replans)
        assert "approval_gate" in export_mermaid()
