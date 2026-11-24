"""FastMCP Server 3: `doc-check` [ALL TOOLS READ-ONLY].

Extracts typed fields from synthetic uploaded MSME certificates and checks cross-document
consistency (legal name, GSTIN state code, investment figures, commencement dates).
"""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from msme_saathi.knowledge.doc_checker import (
    compare_document_fields as _compare_docs,
    extract_document_fields as _extract_fields,
)
from msme_saathi.mcp_servers.schemas import TOOL_PERMISSION_CATALOG

READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_doc_check_mcp(host: str = "127.0.0.1", port: int = 8103) -> FastMCP:
    mcp = FastMCP(
        name="doc-check",
        instructions="Read-only MSME document field extraction and mismatch checker.",
        host=host,
        port=port,
    )

    @mcp.tool(
        name="extract_document_fields",
        description="[READ] Extract structured Udyam, GSTIN, masked PAN, investment, and date fields from synthetic text.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["extract_document_fields"],
    )
    def extract_document_fields(document_id: str, document_type: str, raw_text: str) -> dict[str, Any]:
        try:
            return _extract_fields(document_id=document_id, document_type=document_type, raw_text=raw_text)
        except Exception as exc:
            return {
                "ok": False,
                "document_id": document_id,
                "error": {"code": "DOC_EXTRACT_ERROR", "message": str(exc)},
            }

    @mcp.tool(
        name="compare_document_fields",
        description="[READ] Verify uploaded MSME documents against required checklist and detect field mismatches.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["compare_document_fields"],
    )
    def compare_document_fields(
        profile: dict[str, Any],
        uploaded_docs: list[dict[str, Any]],
        required_docs: list[str],
    ) -> dict[str, Any]:
        try:
            if not isinstance(profile, dict) or not isinstance(uploaded_docs, list) or not isinstance(required_docs, list):
                return {
                    "ok": False,
                    "present_documents": [],
                    "missing_documents": [],
                    "mismatches": [],
                    "requires_human_referral": True,
                    "error": {"code": "INVALID_INPUT", "message": "profile, uploaded_docs, and required_docs must be valid types."},
                }
            return _compare_docs(profile=profile, uploaded_docs=uploaded_docs, required_docs=required_docs)
        except Exception as exc:
            return {
                "ok": False,
                "present_documents": [],
                "missing_documents": list(required_docs or []),
                "mismatches": [],
                "requires_human_referral": True,
                "error": {"code": "DOC_COMPARE_ERROR", "message": str(exc)},
            }

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run doc-check FastMCP server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8103)
    args = parser.parse_args()
    server = create_doc_check_mcp(host=args.host, port=args.port)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
