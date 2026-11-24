"""Persistent SQLite database backing `mock-portal` FastMCP server and FastAPI portal app."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def compute_payload_sha256(payload: dict[str, Any]) -> str:
    """Compute canonical SHA-256 hex digest of an application form payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_approval_token(draft_id: str, payload: dict[str, Any]) -> str:
    """Generate a payload-bound approval token invalidated by any subsequent payload edit."""
    digest = compute_payload_sha256(payload)[:16]
    return f"APPROVED-{draft_id}-SHA256:{digest}"


class MockPortalDB:
    """SQLite database storing non-binding drafts and approved subsidy submissions."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        resolved = db_path or os.getenv("MSME_PORTAL_DB_PATH", "data/mock_portal.sqlite")
        self.db_path = str(resolved)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portal_drafts (
                    draft_id TEXT PRIMARY KEY,
                    enterprise_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portal_submissions (
                    tracking_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    approval_token TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submitted_at TEXT NOT NULL
                )
                """
            )

    def create_draft(
        self,
        enterprise_id: str,
        policy_id: str,
        profile: dict[str, Any],
        revision: int = 1,
    ) -> dict[str, Any]:
        if not enterprise_id or not policy_id:
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_DRAFT_INPUT",
                    "message": "enterprise_id and policy_id are required to create a draft.",
                },
            }

        draft_id = f"DRAFT-{policy_id}-{enterprise_id}-R{revision}"
        payload = {
            "enterprise_id": enterprise_id,
            "enterprise_name": profile.get("enterprise_name") or "Unnamed MSME Unit",
            "state": profile.get("state"),
            "district": profile.get("district"),
            "enterprise_scale": profile.get("enterprise_scale"),
            "plant_machinery_investment_lakhs": profile.get("plant_machinery_investment_lakhs"),
            "policy_id": policy_id,
        }
        payload_hash = compute_payload_sha256(payload)
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO portal_drafts (
                    draft_id, enterprise_id, policy_id, revision, payload_json, payload_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    payload_sha256 = excluded.payload_sha256,
                    created_at = excluded.created_at
                """,
                (
                    draft_id,
                    enterprise_id,
                    policy_id,
                    revision,
                    json.dumps(payload, ensure_ascii=False),
                    payload_hash,
                    now_iso,
                ),
            )

        return {
            "ok": True,
            "draft_id": draft_id,
            "policy_id": policy_id,
            "revision": revision,
            "payload_sha256": payload_hash,
            "payload": payload,
        }

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute(
            "SELECT draft_id, policy_id, revision, payload_json, payload_sha256, created_at FROM portal_drafts WHERE draft_id = ?",
            (draft_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        d_id, p_id, rev, p_json, p_hash, c_at = row
        return {
            "ok": True,
            "draft_id": d_id,
            "policy_id": p_id,
            "revision": rev,
            "payload": json.loads(p_json),
            "payload_sha256": p_hash,
            "created_at": c_at,
        }

    def submit(
        self,
        draft_id: str,
        payload: dict[str, Any],
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        if not approval_token:
            return {
                "ok": False,
                "draft_id": draft_id,
                "status": "rejected_missing_approval",
                "error": {
                    "code": "MISSING_APPROVAL_TOKEN",
                    "message": "Submission blocked: explicit human approval token is required.",
                },
            }

        actual_hash = compute_payload_sha256(payload)
        stored_draft = self.get_draft(draft_id)
        if stored_draft is not None and stored_draft["payload_sha256"] != actual_hash:
            return {
                "ok": False,
                "draft_id": draft_id,
                "status": "rejected_payload_tampered",
                "error": {
                    "code": "PAYLOAD_HASH_MISMATCH",
                    "message": "Submission blocked: payload differs from approved draft; earlier approval is invalid.",
                },
            }

        # Verify hash-bound token if token embeds `-SHA256:<16hex>`
        if "-SHA256:" in approval_token:
            expected_prefix = approval_token.split("-SHA256:", 1)[1]
            if not actual_hash.startswith(expected_prefix):
                return {
                    "ok": False,
                    "draft_id": draft_id,
                    "status": "rejected_invalid_approval_hash",
                    "error": {
                        "code": "TOKEN_PAYLOAD_MISMATCH",
                        "message": "Approval token does not match current form payload digest.",
                    },
                }
        elif not approval_token.startswith(f"APPROVED-{draft_id}"):
            return {
                "ok": False,
                "draft_id": draft_id,
                "status": "rejected_invalid_token",
                "error": {
                    "code": "INVALID_APPROVAL_TOKEN",
                    "message": "Approval token is not bound to this draft_id.",
                },
            }

        tracking_id = f"TRK-{draft_id}"
        now_iso = datetime.now(timezone.utc).isoformat()
        policy_id = str(payload.get("policy_id") or (stored_draft["policy_id"] if stored_draft else "UNKNOWN"))

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO portal_submissions (
                    tracking_id, draft_id, policy_id, payload_json, payload_sha256, approval_token, status, submitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tracking_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    payload_sha256 = excluded.payload_sha256,
                    approval_token = excluded.approval_token,
                    status = excluded.status,
                    submitted_at = excluded.submitted_at
                """,
                (
                    tracking_id,
                    draft_id,
                    policy_id,
                    json.dumps(payload, ensure_ascii=False),
                    actual_hash,
                    approval_token,
                    "submitted",
                    now_iso,
                ),
            )

        return {
            "ok": True,
            "tracking_id": tracking_id,
            "draft_id": draft_id,
            "policy_id": policy_id,
            "payload_sha256": actual_hash,
            "status": "submitted",
            "submitted_at": now_iso,
        }

    def get_status(self, tracking_id: str) -> dict[str, Any]:
        cur = self._conn.execute(
            "SELECT tracking_id, draft_id, policy_id, payload_sha256, status, submitted_at FROM portal_submissions WHERE tracking_id = ?",
            (tracking_id,),
        )
        row = cur.fetchone()
        if not row:
            return {
                "ok": False,
                "tracking_id": tracking_id,
                "status": "not_found",
                "error": {"code": "TRACKING_ID_NOT_FOUND", "message": f"No submission found for {tracking_id}."},
            }
        t_id, d_id, p_id, p_hash, status, sub_at = row
        return {
            "ok": True,
            "tracking_id": t_id,
            "draft_id": d_id,
            "policy_id": p_id,
            "payload_sha256": p_hash,
            "status": status,
            "submitted_at": sub_at,
        }

    def list_submissions(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT tracking_id, draft_id, policy_id, status, submitted_at FROM portal_submissions ORDER BY submitted_at DESC"
        )
        return [
            {
                "tracking_id": r[0],
                "draft_id": r[1],
                "policy_id": r[2],
                "status": r[3],
                "submitted_at": r[4],
            }
            for r in cur.fetchall()
        ]

    def reset(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM portal_submissions")
            self._conn.execute("DELETE FROM portal_drafts")


_DEFAULT_PORTAL_DB: MockPortalDB | None = None


def get_default_portal_db() -> MockPortalDB:
    global _DEFAULT_PORTAL_DB
    if _DEFAULT_PORTAL_DB is None:
        _DEFAULT_PORTAL_DB = MockPortalDB()
    return _DEFAULT_PORTAL_DB
