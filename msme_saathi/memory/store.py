"""Long-term Enterprise Memory Store backed by LangGraph `InMemoryStore` + persistent SQLite.

Enforces the written Memory Policy (`docs/MEMORY_POLICY.md`):
  1. Pseudonymous HMAC-SHA256 enterprise keys (`PSEUDO-...`) — never raw Aadhaar/PAN/bank numbers.
  2. Explicit opt-in consent (`consent=True`) required before any write.
  3. Bounded retention with automatic TTL expiry (`expires_at`, default 90 days).
  4. Strict tenant isolation (cross-enterprise reads/writes denied).
  5. Field-level correction (`correct_profile_fields`) and instant cascade erasure (`forget_me` /
     multilingual "Forget my data" command in English, Hindi, and Marathi).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langgraph.store.memory import InMemoryStore

# Documented allowlist of non-PII profile fields permitted in long-term memory
ALLOWED_PROFILE_FIELDS: frozenset[str] = frozenset(
    {
        "state",
        "district",
        "taluka_zone",
        "sector",
        "enterprise_scale",
        "plant_machinery_investment_lakhs",
        "annual_turnover_lakhs",
        "udyam_registered",
        "commercial_production_date",
        "project_type",
        "solar_upgrade_requested",
    }
)

FORBIDDEN_PII_KEYS: frozenset[str] = frozenset(
    {
        "aadhaar",
        "aadhaar_number",
        "pan",
        "pan_number",
        "bank_account",
        "bank_account_number",
        "ifsc",
        "otp",
        "mobile_number",
    }
)

_AADHAAR_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_BANK_ACCT_RE = re.compile(r"\b\d{11,18}\b")

_FORGET_PATTERNS = [
    re.compile(r"\bforget\s+(?:my\s+data|me)\b", re.I),
    re.compile(r"\bdelete\s+(?:all\s+)?my\s+data\b", re.I),
    re.compile(r"माझा\s+डेटा\s+(?:हटवा|डिलीट|पुसून)", re.I),
    re.compile(r"माझी\s+माहिती\s+(?:हटवा|डिलीट|काढून)", re.I),
    re.compile(r"मेरा\s+डेटा\s+(?:हटा|मिटा|डिलीट)", re.I),
    re.compile(r"मेरी\s+जानकारी\s+(?:हटा|मिटा)", re.I),
]


def pseudonymize_enterprise_id(enterprise_id: str, secret_salt: str | None = None) -> str:
    """Derive an irreversible pseudonymous key from `enterprise_id` using HMAC-SHA256."""
    salt = (secret_salt or os.getenv("MSME_MEMORY_SALT", "msme-saathi-pseudonym-salt-v1")).encode("utf-8")
    digest = hmac.new(salt, enterprise_id.strip().encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"PSEUDO-{digest}"


def scrub_pii_text(text: str) -> str:
    """Mask anything resembling a 12-digit Aadhaar or 11-18 digit bank account number."""
    masked = _AADHAAR_RE.sub("[MASKED-AADHAAR]", text)
    masked = _BANK_ACCT_RE.sub("[MASKED-ACCOUNT]", masked)
    return masked


def is_forget_me_request(user_text: str) -> bool:
    """Detect 'Forget my data' / erasure commands in English, Marathi, or Hindi."""
    return any(pat.search(user_text or "") for pat in _FORGET_PATTERNS)


def _utc_now(override: datetime | None = None) -> datetime:
    if override is not None:
        return override if override.tzinfo else override.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


class EnterpriseMemoryStore:
    """Consent-gated, TTL-expiring long-term store synced between LangGraph `InMemoryStore` and SQLite."""

    def __init__(self, db_path: str | Path = ":memory:", default_ttl_days: int = 90) -> None:
        self.db_path = str(db_path)
        self.default_ttl_days = default_ttl_days
        self.langgraph_store = InMemoryStore()

        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_schema()
        self._hydrate_langgraph_store()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS enterprise_memory (
                    pseudonymous_id TEXT PRIMARY KEY,
                    consent INTEGER NOT NULL,
                    profile_json TEXT NOT NULL,
                    applications_json TEXT NOT NULL,
                    lessons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )

    def _hydrate_langgraph_store(self) -> None:
        cur = self._conn.execute(
            "SELECT pseudonymous_id, consent, profile_json, applications_json, lessons_json, created_at, updated_at, expires_at FROM enterprise_memory"
        )
        for row in cur.fetchall():
            pid, consent, prof_s, apps_s, lessons_s, c_at, u_at, e_at = row
            self.langgraph_store.put(
                ("enterprises", pid),
                "record",
                {
                    "pseudonymous_id": pid,
                    "consent": bool(consent),
                    "profile": json.loads(prof_s),
                    "past_applications": json.loads(apps_s),
                    "lessons": json.loads(lessons_s),
                    "created_at": c_at,
                    "updated_at": u_at,
                    "expires_at": e_at,
                },
            )

    @staticmethod
    def _sanitize_profile(profile: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        dropped_pii = [k for k in profile if k.lower() in FORBIDDEN_PII_KEYS]
        clean: dict[str, Any] = {}
        for k, v in profile.items():
            if k in ALLOWED_PROFILE_FIELDS and v is not None:
                clean[k] = scrub_pii_text(v) if isinstance(v, str) else v
        return clean, dropped_pii

    def save_profile(
        self,
        actor_enterprise_id: str,
        profile: dict[str, Any],
        *,
        consent: bool,
        target_enterprise_id: str | None = None,
        ttl_days: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist non-PII enterprise profile fields only when explicit `consent=True` is provided."""
        if target_enterprise_id and target_enterprise_id != actor_enterprise_id:
            return {
                "ok": False,
                "saved": False,
                "error": {"code": "TENANT_ACCESS_DENIED", "message": "Cannot modify another enterprise's memory."},
            }

        if not consent:
            return {
                "ok": False,
                "saved": False,
                "error": {
                    "code": "CONSENT_REQUIRED",
                    "message": "Explicit citizen/entrepreneur consent is required before saving long-term memory.",
                },
            }

        ts = _utc_now(now)
        exp = ts + timedelta(days=ttl_days if ttl_days is not None else self.default_ttl_days)
        pid = pseudonymize_enterprise_id(actor_enterprise_id)
        sanitized, dropped_pii = self._sanitize_profile(profile)

        existing = self._read_raw(pid)
        created_at = existing["created_at"] if existing else ts.isoformat()
        apps = existing["past_applications"] if existing else []
        lessons = existing["lessons"] if existing else []
        merged_profile = {**(existing["profile"] if existing else {}), **sanitized}

        record = {
            "pseudonymous_id": pid,
            "consent": True,
            "profile": merged_profile,
            "past_applications": apps,
            "lessons": lessons,
            "created_at": created_at,
            "updated_at": ts.isoformat(),
            "expires_at": exp.isoformat(),
        }
        self._write_raw(record)
        return {
            "ok": True,
            "saved": True,
            "pseudonymous_id": pid,
            "stored_fields": sorted(merged_profile.keys()),
            "dropped_pii_fields": dropped_pii,
            "expires_at": record["expires_at"],
        }

    def record_application(
        self,
        actor_enterprise_id: str,
        *,
        tracking_id: str,
        policy_id: str,
        status: str = "submitted",
        consent: bool = True,
        ttl_days: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Store an application tracking ID under consented memory for later follow-up queries."""
        if not consent:
            return {
                "ok": False,
                "saved": False,
                "error": {"code": "CONSENT_REQUIRED", "message": "Consent required to save application history."},
            }
        ts = _utc_now(now)
        exp = ts + timedelta(days=ttl_days if ttl_days is not None else self.default_ttl_days)
        pid = pseudonymize_enterprise_id(actor_enterprise_id)
        existing = self._read_raw(pid)
        profile = existing["profile"] if existing else {}
        apps = list(existing["past_applications"]) if existing else []
        lessons = existing["lessons"] if existing else []
        created_at = existing["created_at"] if existing else ts.isoformat()

        apps = [a for a in apps if a.get("tracking_id") != tracking_id]
        apps.append(
            {
                "tracking_id": tracking_id,
                "policy_id": policy_id,
                "status": status,
                "recorded_at": ts.isoformat(),
            }
        )

        record = {
            "pseudonymous_id": pid,
            "consent": True,
            "profile": profile,
            "past_applications": apps,
            "lessons": lessons,
            "created_at": created_at,
            "updated_at": ts.isoformat(),
            "expires_at": exp.isoformat(),
        }
        self._write_raw(record)
        return {"ok": True, "saved": True, "pseudonymous_id": pid, "tracking_id": tracking_id}

    def load_memory(
        self,
        actor_enterprise_id: str,
        *,
        target_enterprise_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Load consented profile & past applications if unexpired and owned by `actor_enterprise_id`."""
        if target_enterprise_id and target_enterprise_id != actor_enterprise_id:
            return {
                "ok": False,
                "found": False,
                "error": {
                    "code": "TENANT_ACCESS_DENIED",
                    "message": "Cross-enterprise memory lookup is strictly prohibited.",
                },
            }

        pid = pseudonymize_enterprise_id(actor_enterprise_id)
        record = self._read_raw(pid)
        if not record:
            return {"ok": True, "found": False, "pseudonymous_id": pid, "profile": {}, "past_applications": []}

        ts = _utc_now(now)
        exp = datetime.fromisoformat(record["expires_at"])
        if ts >= exp:
            self._delete_raw(pid)
            return {
                "ok": True,
                "found": False,
                "expired": True,
                "pseudonymous_id": pid,
                "profile": {},
                "past_applications": [],
            }

        return {
            "ok": True,
            "found": True,
            "expired": False,
            "pseudonymous_id": pid,
            "consent": record["consent"],
            "profile": dict(record["profile"]),
            "past_applications": list(record["past_applications"]),
            "expires_at": record["expires_at"],
        }

    def correct_profile_fields(
        self,
        actor_enterprise_id: str,
        updates: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Allow an entrepreneur or DIC officer to correct stored profile attributes."""
        pid = pseudonymize_enterprise_id(actor_enterprise_id)
        existing = self._read_raw(pid)
        if not existing:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "No stored profile found to update."}}

        sanitized, dropped_pii = self._sanitize_profile(updates)
        ts = _utc_now(now)
        existing["profile"].update(sanitized)
        existing["updated_at"] = ts.isoformat()
        self._write_raw(existing)
        return {
            "ok": True,
            "pseudonymous_id": pid,
            "updated_fields": sorted(sanitized.keys()),
            "dropped_pii_fields": dropped_pii,
            "profile": dict(existing["profile"]),
        }

    def forget_me(self, actor_enterprise_id: str) -> dict[str, Any]:
        """Immediately erase all stored profile attributes, past applications, and lessons."""
        pid = pseudonymize_enterprise_id(actor_enterprise_id)
        existed = self._read_raw(pid) is not None
        self._delete_raw(pid)
        return {
            "ok": True,
            "deleted": existed,
            "pseudonymous_id": pid,
            "message": "All stored household/enterprise profile and past application records have been permanently deleted.",
        }

    def handle_user_command_if_forget(self, actor_enterprise_id: str, user_text: str) -> dict[str, Any] | None:
        """If `user_text` asks to delete/forget data (EN/HI/MR), execute `forget_me` and return receipt."""
        if is_forget_me_request(user_text):
            return self.forget_me(actor_enterprise_id)
        return None

    # --- Playbook lessons namespace (shared non-PII Reflexion lessons for Role 3) ---
    def save_reviewed_lesson(self, lesson_id: str, lesson_text: str, applies_when: str, reviewer: str) -> dict[str, Any]:
        payload = {
            "lesson_id": lesson_id,
            "lesson_text": scrub_pii_text(lesson_text),
            "applies_when": applies_when,
            "reviewer": reviewer,
            "reviewed": True,
        }
        self.langgraph_store.put(("playbook", "lessons"), lesson_id, payload)
        return {"ok": True, **payload}

    def list_reviewed_lessons(self) -> list[dict[str, Any]]:
        items = self.langgraph_store.search(("playbook", "lessons"))
        return [it.value for it in items]

    def _read_raw(self, pid: str) -> dict[str, Any] | None:
        item = self.langgraph_store.get(("enterprises", pid), "record")
        if item is not None:
            return dict(item.value)
        cur = self._conn.execute(
            "SELECT pseudonymous_id, consent, profile_json, applications_json, lessons_json, created_at, updated_at, expires_at FROM enterprise_memory WHERE pseudonymous_id = ?",
            (pid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        _, consent, prof_s, apps_s, lessons_s, c_at, u_at, e_at = row
        rec = {
            "pseudonymous_id": pid,
            "consent": bool(consent),
            "profile": json.loads(prof_s),
            "past_applications": json.loads(apps_s),
            "lessons": json.loads(lessons_s),
            "created_at": c_at,
            "updated_at": u_at,
            "expires_at": e_at,
        }
        self.langgraph_store.put(("enterprises", pid), "record", rec)
        return rec

    def _write_raw(self, record: dict[str, Any]) -> None:
        pid = record["pseudonymous_id"]
        self.langgraph_store.put(("enterprises", pid), "record", record)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO enterprise_memory (
                    pseudonymous_id, consent, profile_json, applications_json, lessons_json, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pseudonymous_id) DO UPDATE SET
                    consent = excluded.consent,
                    profile_json = excluded.profile_json,
                    applications_json = excluded.applications_json,
                    lessons_json = excluded.lessons_json,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    pid,
                    1 if record["consent"] else 0,
                    json.dumps(record["profile"], ensure_ascii=False),
                    json.dumps(record["past_applications"], ensure_ascii=False),
                    json.dumps(record["lessons"], ensure_ascii=False),
                    record["created_at"],
                    record["updated_at"],
                    record["expires_at"],
                ),
            )

    def _delete_raw(self, pid: str) -> None:
        self.langgraph_store.delete(("enterprises", pid), "record")
        with self._conn:
            self._conn.execute("DELETE FROM enterprise_memory WHERE pseudonymous_id = ?", (pid,))

    def close(self) -> None:
        self._conn.close()
