"""Small standalone FastAPI application for the Mock MSME Subsidy Portal (`mock-portal`).

Matches Capstone Guide Figure 2 & Page 8:
  "mock-portal (draft, submit, status; a small FastAPI app with its own database)."
"""

from __future__ import annotations

from fastapi import FastAPI

from msme_saathi.mcp_servers.portal_db import MockPortalDB, get_default_portal_db
from msme_saathi.mcp_servers.schemas import CreateDraftInput, SubmitApplicationInput


def create_portal_app(db: MockPortalDB | None = None) -> FastAPI:
    portal_db = db or get_default_portal_db()
    app = FastAPI(
        title="MSME-Incentive Saathi — Mock Subsidy Portal API",
        version="0.2.0",
        description="Official-style mock DIC/MSME portal storing subsidy drafts and human-approved submissions.",
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "service": "mock-portal-api"}

    @app.post("/drafts")
    def create_draft_endpoint(req: CreateDraftInput) -> dict[str, object]:
        return portal_db.create_draft(
            enterprise_id=req.enterprise_id,
            policy_id=req.policy_id,
            profile=req.profile,
            revision=req.revision,
        )

    @app.get("/drafts/{draft_id}")
    def get_draft_endpoint(draft_id: str) -> dict[str, object]:
        draft = portal_db.get_draft(draft_id)
        if draft is None:
            return {"ok": False, "draft_id": draft_id, "status": "not_found"}
        return draft

    @app.post("/submissions")
    def submit_endpoint(req: SubmitApplicationInput) -> dict[str, object]:
        return portal_db.submit(
            draft_id=req.draft_id,
            payload=req.payload,
            approval_token=req.approval_token,
        )

    @app.get("/status/{tracking_id}")
    def status_endpoint(tracking_id: str) -> dict[str, object]:
        return portal_db.get_status(tracking_id)

    @app.get("/submissions")
    def list_submissions_endpoint() -> dict[str, object]:
        return {"ok": True, "submissions": portal_db.list_submissions()}

    return app


app = create_portal_app()
