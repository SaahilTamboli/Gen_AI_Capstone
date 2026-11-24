"""`langchain-mcp-adapters` integration & per-agent READ/WRITE permission enforcement.

Loads live FastMCP tools over `stdio` (development) or `streamable_http` (Docker Compose)
via `MultiServerMCPClient`, and enforces least-privilege tool access per specialist agent.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from msme_saathi.mcp_servers.schemas import TOOL_PERMISSION_CATALOG

TransportMode = Literal["stdio", "http"]

_REPO_ROOT = str(Path(__file__).resolve().parents[2])

_SERVER_MODULES = {
    "msme-policies-kb": ("msme_saathi.mcp_servers.policies_kb_server", "MSME_POLICIES_KB_URL", "http://policies-kb:8101/mcp"),
    "rule-engine": ("msme_saathi.mcp_servers.rule_engine_server", "MSME_RULE_ENGINE_URL", "http://rule-engine:8102/mcp"),
    "doc-check": ("msme_saathi.mcp_servers.doc_check_server", "MSME_DOC_CHECK_URL", "http://doc-check:8103/mcp"),
    "mock-portal": ("msme_saathi.mcp_servers.mock_portal_server", "MSME_MOCK_PORTAL_URL", "http://mock-portal:8104/mcp"),
}


def build_mcp_connections(
    mode: TransportMode = "stdio",
    servers: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build `MultiServerMCPClient` connection specs for `stdio` (dev) or `http` (Docker)."""
    selected = servers or list(_SERVER_MODULES.keys())
    connections: dict[str, dict[str, Any]] = {}
    env = {**os.environ, "PYTHONPATH": _REPO_ROOT}

    for srv in selected:
        module_name, env_var, default_url = _SERVER_MODULES[srv]
        if mode == "http":
            connections[srv] = {
                "transport": "streamable_http",
                "url": os.getenv(env_var, default_url),
            }
        else:
            connections[srv] = {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", module_name, "--transport", "stdio"],
                "env": env,
            }
    return connections


def check_tool_permission(agent_name: str, tool_name: str) -> dict[str, Any]:
    """Deterministic pre-tool permission gate verifying READ/WRITE access per agent."""
    spec = TOOL_PERMISSION_CATALOG.get(tool_name)
    if spec is None:
        return {
            "allowed": False,
            "agent": agent_name,
            "tool": tool_name,
            "access": "unknown",
            "reason": f"Tool '{tool_name}' is not registered in TOOL_PERMISSION_CATALOG.",
        }

    allowed_agents = spec["allowed_agents"]
    if agent_name not in allowed_agents:
        return {
            "allowed": False,
            "agent": agent_name,
            "tool": tool_name,
            "access": spec["access"],
            "reason": (
                f"Agent '{agent_name}' is denied {spec['access'].upper()} access to '{tool_name}'. "
                f"Permitted agents: {allowed_agents}."
            ),
        }

    return {
        "allowed": True,
        "agent": agent_name,
        "tool": tool_name,
        "access": spec["access"],
        "server": spec["server"],
    }


def allowed_tool_names_for_agent(agent_name: str) -> list[str]:
    """Return the exact allow-listed tool names permitted for `agent_name`."""
    return [
        t_name
        for t_name, meta in TOOL_PERMISSION_CATALOG.items()
        if agent_name in meta["allowed_agents"]
    ]


async def load_agent_mcp_tools(
    agent_name: str,
    mode: TransportMode = "stdio",
) -> list[BaseTool]:
    """Load LangChain `BaseTool` instances from FastMCP servers filtered strictly for `agent_name`."""
    allowed_names = set(allowed_tool_names_for_agent(agent_name))
    if not allowed_names:
        return []

    needed_servers = sorted({TOOL_PERMISSION_CATALOG[t]["server"] for t in allowed_names})
    client = MultiServerMCPClient(build_mcp_connections(mode=mode, servers=needed_servers))
    all_tools = await client.get_tools()
    return [tool for tool in all_tools if tool.name in allowed_names]


async def call_mcp_tool_async(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    mode: TransportMode = "stdio",
) -> Any:
    """Invoke a FastMCP tool over the live MCP protocol (`stdio` or `streamable_http`)."""
    client = MultiServerMCPClient(build_mcp_connections(mode=mode, servers=[server_name]))
    tools = await client.get_tools()
    by_name = {t.name: t for t in tools}
    if tool_name not in by_name:
        raise KeyError(f"Tool '{tool_name}' not exposed by server '{server_name}'")
    raw = await by_name[tool_name].ainvoke(arguments)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "text" in raw[0]:
        parsed_items = []
        for item in raw:
            try:
                parsed_items.append(json.loads(item["text"]))
            except Exception:
                parsed_items.append(item["text"])
        return parsed_items if len(parsed_items) > 1 else parsed_items[0]
    return raw


def call_mcp_tool_sync(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    mode: TransportMode = "stdio",
) -> Any:
    """Synchronous wrapper around `call_mcp_tool_async`."""
    return asyncio.run(call_mcp_tool_async(server_name, tool_name, arguments, mode=mode))
