"""FastMCP Server 2: `rule-engine` [ALL TOOLS READ-ONLY — Deterministic Python Code].

Evaluates `EnterpriseProfile` dictionaries against human-reviewed JSON rules (`scheme_rules.json`)
and computes exact rupee grant estimates in tested Python arithmetic.
"""

from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from msme_saathi.knowledge.rule_engine import get_default_rule_engine
from msme_saathi.mcp_servers.schemas import TOOL_PERMISSION_CATALOG

READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_rule_engine_mcp(host: str = "127.0.0.1", port: int = 8102) -> FastMCP:
    mcp = FastMCP(
        name="rule-engine",
        instructions="Deterministic MSME eligibility & subsidy calculator. Never uses an LLM.",
        host=host,
        port=port,
    )

    @mcp.tool(
        name="evaluate_eligibility",
        description="[READ] Deterministically evaluate an EnterpriseProfile against a policy's reviewed JSON rules.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["evaluate_eligibility"],
    )
    def evaluate_eligibility(profile: dict[str, Any], policy_id: str) -> dict[str, Any]:
        try:
            if not isinstance(profile, dict) or not policy_id:
                return {
                    "ok": False,
                    "policy_id": str(policy_id),
                    "verdict": "cant_tell",
                    "deciding_clause": "INVALID_INPUT",
                    "missing_fields": [],
                    "estimated_subsidy_inr": None,
                    "reason": "Structured profile dict and non-empty policy_id are required.",
                    "error": {"code": "INVALID_INPUT", "message": "Invalid profile or policy_id."},
                }
            return get_default_rule_engine().evaluate(profile=profile, policy_id=policy_id)
        except Exception as exc:
            return {
                "ok": False,
                "policy_id": str(policy_id),
                "verdict": "cant_tell",
                "deciding_clause": f"{policy_id}/Error",
                "missing_fields": [],
                "estimated_subsidy_inr": None,
                "reason": f"Rule evaluation error: {exc}",
                "error": {"code": "RULE_ENGINE_ERROR", "message": str(exc)},
            }

    @mcp.tool(
        name="get_policy_rules",
        description="[READ] Inspect the clause-linked JSON conditions and subsidy formula for `policy_id`.",
        annotations=READ_ANNOTATIONS,
        meta=TOOL_PERMISSION_CATALOG["get_policy_rules"],
    )
    def get_policy_rules(policy_id: str) -> dict[str, Any]:
        try:
            rules = get_default_rule_engine().get_policy_rules(policy_id=policy_id)
            if rules is None:
                return {
                    "ok": False,
                    "policy_id": policy_id,
                    "error": {"code": "POLICY_NOT_FOUND", "message": f"No rule spec found for '{policy_id}'."},
                }
            return {"ok": True, **rules}
        except Exception as exc:
            return {
                "ok": False,
                "policy_id": policy_id,
                "error": {"code": "GET_RULES_ERROR", "message": str(exc)},
            }

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run rule-engine FastMCP server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8102)
    args = parser.parse_args()
    server = create_rule_engine_mcp(host=args.host, port=args.port)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
