"""FastMCP Server 4: `mock-portal` [draft/status=READ, submit_application=WRITE].

`submit_application` is the ONLY world-changing tool in the entire MSME-Incentive Saathi
system. It is explicitly marked `readOnlyHint=False, destructiveHint=True`, restricted to
`application_submit` / `application_agent`, and rejects missing or tampered approval tokens.
"""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from msme_saathi.mcp_servers.portal_db import get_default_portal_db
from msme_saathi.mcp_servers.schemas import TOOL_PERMISSION_CATALOG

READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


def create_mock_portal_mcp(host: str = "127.0.0.1", port: int = 8104) -> FastMCP:
    mcp = FastMCP(
        name="mock-portal",
        instructions="MSME Subsidy Mock Portal. `submit_application` is WRITE-protected behind human approval.",
        host=host,
        port=port,
    )

    @mcp.tool(
        name="create_application_draft",
        description="[READ] Prepare a non-binding subsidy application draft with SHA-256 payload digest.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["create_application_draft"],
    )
    def create_application_draft(
        enterprise_id: str,
        policy_id: str,
        profile: dict[str, Any],
        revision: int = 1,
    ) -> dict[str, Any]:
        try:
            return get_default_portal_db().create_draft(
                enterprise_id=enterprise_id,
                policy_id=policy_id,
                profile=profile,
                revision=revision,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": {"code": "CREATE_DRAFT_ERROR", "message": str(exc)},
            }

    @mcp.tool(
        name="submit_application",
        description="[WRITE] Submit an approved subsidy application draft to the Portal DB (requires valid approval_token).",
        annotations=WRITE_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["submit_application"],
    )
    def submit_application(
        draft_id: str,
        payload: dict[str, Any],
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        try:
            if not draft_id or not isinstance(payload, dict):
                return {
                    "ok": False,
                    "draft_id": str(draft_id),
                    "status": "rejected_invalid_input",
                    "error": {"code": "INVALID_INPUT", "message": "draft_id and payload dict are required."},
                }
            return get_default_portal_db().submit(
                draft_id=draft_id,
                payload=payload,
                approval_token=approval_token,
            )
        except Exception as exc:
            return {
                "ok": False,
                "draft_id": str(draft_id),
                "status": "error",
                "error": {"code": "SUBMIT_PORTAL_ERROR", "message": str(exc)},
            }

    @mcp.tool(
        name="get_application_status",
        description="[READ] Query current status of a submitted MSME subsidy application by `tracking_id`.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["get_application_status"],
    )
    def get_application_status(tracking_id: str) -> dict[str, Any]:
        try:
            if not tracking_id:
                return {
                    "ok": False,
                    "tracking_id": tracking_id,
                    "status": "invalid_input",
                    "error": {"code": "INVALID_INPUT", "message": "tracking_id is required."},
                }
            return get_default_portal_db().get_status(tracking_id=tracking_id)
        except Exception as exc:
            return {
                "ok": False,
                "tracking_id": tracking_id,
                "status": "error",
                "error": {"code": "STATUS_LOOKUP_ERROR", "message": str(exc)},
            }

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run mock-portal FastMCP server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8104)
    args = parser.parse_args()
    server = create_mock_portal_mcp(host=args.host, port=args.port)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
