"""Starts the real MCP server (stdio) on the demo warehouse and calls every tool once.

Run after ``make demo``: it needs the role databases (``make roles``) and the catalog
(``make catalog``). The analyst's request for ``health_notes`` must come back as an error.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def main() -> int:
    """Return 0 when every tool behaves."""
    env = {**os.environ, "PLATFORM_ROLE": "analyst", "PLATFORM_PRINCIPAL": "smoke-test"}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cross_product_platform.cli", "serve-mcp"],
        cwd=ROOT,
        env=env,
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = sorted(t.name for t in (await session.list_tools()).tools)
        assert tools == ["describe_table", "get_metric", "run_query", "search_tables"], tools
        found = await session.call_tool("search_tables", {"query": "payment"})
        assert found.structured_content and found.structured_content["tables"], found
        described = await session.call_tool("describe_table", {"name": "dim_member"})
        assert described.structured_content is not None
        names = [c["name"] for c in described.structured_content["columns"]]
        assert "email_domain" in names and "email" not in names, names
        metric = await session.call_tool("get_metric", {"name": "active_members"})
        assert metric.structured_content and metric.structured_content["latest"], metric
        rows = await session.call_tool("run_query", {"sql": "select count(*) as n from dim_member"})
        assert rows.is_error is False and rows.structured_content, rows
        denied = await session.call_tool(
            "run_query", {"sql": "select health_notes from dim_member"}
        )
        assert denied.is_error is True, denied
        print(
            "mcp smoke ok: 4 tools, latest metric returned, analyst asking for health_notes refused"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
