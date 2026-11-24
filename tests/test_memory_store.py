"""Tests for consent-gated long-term EnterpriseMemoryStore, TTL expiry, tenant isolation, Forget-Me, and context trimming."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from msme_saathi.memory import (
    EnterpriseMemoryStore,
    pseudonymize_enterprise_id,
    trim_or_summarize_messages,
)


def test_memory_requires_consent_and_scrubs_pii(tmp_path: Path):
    store = EnterpriseMemoryStore(db_path=tmp_path / "mem.sqlite")

    denied = store.save_profile("ENT-101", {"state": "Maharashtra"}, consent=False)
    assert denied["ok"] is False
    assert denied["error"]["code"] == "CONSENT_REQUIRED"
    assert store.load_memory("ENT-101")["found"] is False

    saved = store.save_profile(
        "ENT-101",
        {
            "state": "Maharashtra",
            "district": "Akola",
            "enterprise_scale": "micro",
            "aadhaar_number": "1234 5678 9012",
            "bank_account_number": "00112233445566",
        },
        consent=True,
    )
    assert saved["ok"] is True
    assert saved["pseudonymous_id"] == pseudonymize_enterprise_id("ENT-101")
    assert set(saved["dropped_pii_fields"]) == {"aadhaar_number", "bank_account_number"}
    store.close()

    # Reopen SQLite to verify persistent hydration into LangGraph store
    store2 = EnterpriseMemoryStore(db_path=tmp_path / "mem.sqlite")
    loaded = store2.load_memory("ENT-101")
    assert loaded["found"] is True
    assert loaded["profile"]["district"] == "Akola"
    assert "aadhaar_number" not in loaded["profile"]
    store2.close()


def test_memory_ttl_expiry_and_cross_tenant_isolation(tmp_path: Path):
    store = EnterpriseMemoryStore(db_path=tmp_path / "ttl.sqlite", default_ttl_days=30)
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    store.save_profile("ENT-A", {"state": "Maharashtra", "district": "Pune"}, consent=True, now=t0)

    # Cross-tenant read attempt from ENT-B -> strictly blocked
    cross = store.load_memory("ENT-B", target_enterprise_id="ENT-A", now=t0 + timedelta(days=5))
    assert cross["ok"] is False
    assert cross["error"]["code"] == "TENANT_ACCESS_DENIED"

    # Before expiry (day 29) -> found
    assert store.load_memory("ENT-A", now=t0 + timedelta(days=29))["found"] is True

    # After expiry (day 31) -> purged automatically
    expired = store.load_memory("ENT-A", now=t0 + timedelta(days=31))
    assert expired["found"] is False
    assert expired["expired"] is True
    store.close()


def test_memory_correction_and_multilingual_forget_me(tmp_path: Path):
    store = EnterpriseMemoryStore(db_path=tmp_path / "forget.sqlite")
    store.save_profile("ENT-DEL", {"state": "Maharashtra", "plant_machinery_investment_lakhs": 30.0}, consent=True)
    store.record_application("ENT-DEL", tracking_id="TRK-001", policy_id="MH-PSI-2019-CAP", consent=True)

    upd = store.correct_profile_fields("ENT-DEL", {"plant_machinery_investment_lakhs": 45.0})
    assert upd["ok"] is True
    assert upd["profile"]["plant_machinery_investment_lakhs"] == 45.0

    # Hindi "Forget my data" command erases profile + past applications
    receipt = store.handle_user_command_if_forget("ENT-DEL", "कृपया मेरा डेटा हटा दें")
    assert receipt is not None and receipt["deleted"] is True
    assert store.load_memory("ENT-DEL")["found"] is False
    store.close()


def test_short_term_context_trimmer_masks_pii_and_summarizes_old_turns():
    msgs = [SystemMessage(content="You are MSME Saathi.")]
    for i in range(10):
        msgs.append(HumanMessage(content=f"Turn {i}: Aadhaar 4455 6677 8899 and machinery investment {10 + i} lakhs"))
        msgs.append(AIMessage(content=f"Reply {i}: noted profile update."))

    trimmed = trim_or_summarize_messages(msgs, max_recent_messages=4, max_tokens=400)
    assert len(trimmed) < len(msgs)
    summary_text = str(trimmed[1].content)
    assert "[Trimmed Short-Term Context Summary]" in summary_text
    assert "4455 6677 8899" not in summary_text
    assert "[MASKED-AADHAAR]" in summary_text
