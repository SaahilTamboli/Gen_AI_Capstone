"""MCP tools, `langchain-mcp-adapters` client loader, and per-agent READ/WRITE permissions."""

from msme_saathi.tools.mcp_client import (
    allowed_tool_names_for_agent,
    build_mcp_connections,
    call_mcp_tool_async,
    call_mcp_tool_sync,
    check_tool_permission,
    load_agent_mcp_tools,
)
from msme_saathi.tools.mcp_stubs import (
    compare_document_fields,
    create_application_draft,
    evaluate_eligibility,
    extract_document_fields,
    get_application_status,
    get_document_checklist,
    get_policy_clause,
    get_policy_rules,
    reset_portal_state,
    search_policies,
    submit_application,
)

__all__ = [
    "allowed_tool_names_for_agent",
    "build_mcp_connections",
    "call_mcp_tool_async",
    "call_mcp_tool_sync",
    "check_tool_permission",
    "compare_document_fields",
    "create_application_draft",
    "evaluate_eligibility",
    "extract_document_fields",
    "get_application_status",
    "get_document_checklist",
    "get_policy_clause",
    "get_policy_rules",
    "load_agent_mcp_tools",
    "reset_portal_state",
    "search_policies",
    "submit_application",
]
