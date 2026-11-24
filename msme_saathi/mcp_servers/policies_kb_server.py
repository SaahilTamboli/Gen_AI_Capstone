"""FastMCP Server 1: `msme-policies-kb` [ALL TOOLS READ-ONLY].

Provides hybrid search (BM25 + Chroma vectors + RRF + Reranker + multilingual query rewriting),
exact clause retrieval with provenance/dates, and document checklists.
Supports `stdio` (development) and `streamable-http` / `sse` (Docker).
"""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from msme_saathi.knowledge.hybrid_retriever import get_default_retriever
from msme_saathi.mcp_servers.schemas import TOOL_PERMISSION_CATALOG

READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_policies_kb_mcp(host: str = "127.0.0.1", port: int = 8101) -> FastMCP:
    mcp = FastMCP(
        name="msme-policies-kb",
        instructions="Read-only MSME policy knowledge base over 42 Central & Maharashtra industrial schemes.",
        host=host,
        port=port,
    )

    @mcp.tool(
        name="search_policies",
        description="[READ] Hybrid BM25 + Chroma vector RRF + reranked search over official MSME scheme clauses in EN/HI/MR.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["search_policies"],
    )
    def search_policies(
        query: str,
        state: str | None = None,
        district_tier: str | None = None,
        enterprise_scale: str | None = None,
        sector: str | None = None,
        broadened: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            if not isinstance(query, str):
                return []
            retriever = get_default_retriever()
            return retriever.search_policies(
                query=query,
                state=state,
                district_tier=district_tier,
                enterprise_scale=enterprise_scale,
                sector=sector,
                broadened=broadened,
            )
        except Exception as exc:
            return [
                {
                    "ok": False,
                    "error": {"code": "SEARCH_POLICIES_ERROR", "message": str(exc)},
                }
            ]

    @mcp.tool(
        name="get_policy_clause",
        description="[READ] Fetch exact official clause wording, source URL, collection date, and validity date.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["get_policy_clause"],
    )
    def get_policy_clause(policy_id: str, clause_id: str) -> dict[str, Any]:
        try:
            if not policy_id or not clause_id:
                return {
                    "ok": False,
                    "policy_id": policy_id,
                    "clause_id": clause_id,
                    "error": {"code": "INVALID_INPUT", "message": "policy_id and clause_id are required."},
                }
            res = get_default_retriever().get_policy_clause(policy_id=policy_id, clause_id=clause_id)
            if res is None:
                return {
                    "ok": False,
                    "policy_id": policy_id,
                    "clause_id": clause_id,
                    "error": {"code": "CLAUSE_NOT_FOUND", "message": f"Unknown policy_id '{policy_id}'."},
                }
            return {"ok": True, **res}
        except Exception as exc:
            return {
                "ok": False,
                "policy_id": policy_id,
                "clause_id": clause_id,
                "error": {"code": "GET_CLAUSE_ERROR", "message": str(exc)},
            }

    @mcp.tool(
        name="get_document_checklist",
        description="[READ] Retrieve mandatory compliance documents required to apply for `policy_id`.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["get_document_checklist"],
    )
    def get_document_checklist(policy_id: str) -> dict[str, Any]:
        try:
            if not policy_id:
                return {
                    "ok": False,
                    "policy_id": policy_id,
                    "required_documents": [],
                    "error": {"code": "INVALID_INPUT", "message": "policy_id is required."},
                }
            res = get_default_retriever().get_document_checklist(policy_id=policy_id)
            if res is None:
                return {
                    "ok": False,
                    "policy_id": policy_id,
                    "required_documents": [],
                    "error": {"code": "POLICY_NOT_FOUND", "message": f"Unknown policy_id '{policy_id}'."},
                }
            return {"ok": True, **res}
        except Exception as exc:
            return {
                "ok": False,
                "policy_id": policy_id,
                "required_documents": [],
                "error": {"code": "CHECKLIST_ERROR", "message": str(exc)},
            }

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run msme-policies-kb FastMCP server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    server = create_policies_kb_mcp(host=args.host, port=args.port)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
