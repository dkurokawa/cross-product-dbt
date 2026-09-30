"""The MCP server, exercised through a real stdio client (subprocess round trip)."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .gateway_support import Env, make_env

PRINCIPAL = "claude-desktop"


def _catalog(env: Env) -> Path:
    directory = env.root / "catalog"
    (directory / "roles").mkdir(parents=True)
    members = {
        "analyst": ["member_key", "email_domain", "n_products"],
        "privacy_officer": ["member_key", "health_notes", "n_products"],
    }
    for role, columns in members.items():
        tables: list[dict[str, Any]] = [
            {
                "name": "dim_member",
                "description": "One row per real person across products.",
                "columns": [
                    {"name": c, "type": "VARCHAR", "description": "", "pii": "none"}
                    for c in columns
                ],
            }
        ]
        metrics: list[dict[str, Any]] = []
        if role == "analyst":
            tables.append(
                {
                    "name": "metric_active_members",
                    "description": "Members who did something in the last 30 days.",
                    "columns": [
                        {"name": "period_month", "type": "DATE", "description": "", "pii": "none"}
                    ],
                }
            )
            metrics.append(
                {
                    "name": "active_members",
                    "description": "Members active in 30 days.",
                    "model": "metric_active_members",
                    "known_variants": [{"product": "A", "reported_as": "active_users"}],
                }
            )
        (directory / "roles" / f"{role}.json").write_text(
            json.dumps({"role": role, "tables": tables, "metrics": metrics}), encoding="utf-8"
        )
    return directory


def _params(env: Env, role: str, principal: str = PRINCIPAL) -> StdioServerParameters:
    catalog = _catalog(env) if not (env.root / "catalog").exists() else env.root / "catalog"
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "cross_product_platform.cli",
            "serve-mcp",
            "--allowlist",
            str(env.allowlist_path),
            "--warehouse-dir",
            str(env.warehouse_dir),
            "--audit-root",
            str(env.audit_root),
            "--catalog-dir",
            str(catalog),
        ],  # fmt: skip
        env={**os.environ, "PLATFORM_ROLE": role, "PLATFORM_PRINCIPAL": principal},
    )


def _run(env: Env, role: str, work: Callable[[ClientSession], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with (
            stdio_client(_params(env, role)) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await work(session)

    return asyncio.run(asyncio.wait_for(go(), timeout=60))


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


def test_exactly_four_read_only_tools_and_none_takes_a_role(env: Env) -> None:
    async def work(s: ClientSession) -> Any:
        return await s.list_tools()

    tools = _run(env, "analyst", work).tools
    assert sorted(t.name for t in tools) == [
        "describe_table", "get_metric", "run_query", "search_tables",
    ]  # fmt: skip
    for tool in tools:
        arguments = set(tool.input_schema.get("properties", {}))
        assert not arguments & {"role", "principal", "user", "database"}, tool.name


def test_search_and_describe_tables(env: Env) -> None:
    async def work(s: ClientSession) -> Any:
        found = await s.call_tool("search_tables", {"query": "person members"})
        described = await s.call_tool("describe_table", {"name": "dim_member"})
        missing = await s.call_tool("describe_table", {"name": "fct_payment"})
        return found, described, missing

    found, described, missing = _run(env, "analyst", work)
    assert found.is_error is False
    hit = found.structured_content["tables"][0]
    assert hit["name"] == "dim_member" and "member_key" in hit["matching_columns"]
    assert described.structured_content["columns"][1]["name"] == "email_domain"
    assert missing.is_error is True and "unknown table" in missing.content[0].text


def test_get_metric_returns_definition_variants_and_latest_value(env: Env) -> None:
    async def work(s: ClientSession) -> Any:
        return (
            await s.call_tool("get_metric", {"name": "active_members"}),
            await s.call_tool("get_metric", {"name": "net_revenue"}),
        )

    ok, unknown = _run(env, "analyst", work)
    body = ok.structured_content
    assert body["description"] == "Members active in 30 days."
    assert body["known_variants"][0]["reported_as"] == "active_users"
    assert body["latest"] == [
        {"period_month": "2026-09-01", "product": "A", "value": 7},
        {"period_month": "2026-09-01", "product": "ALL", "value": 12},
    ]
    assert unknown.is_error is True
    (event,) = env.audit("query_executed")
    assert event["principal"] == PRINCIPAL and event["referenced_tables"] == [
        "metric_active_members"
    ]


def test_run_query_returns_rows_and_is_audited_with_the_startup_principal(env: Env) -> None:
    async def work(s: ClientSession) -> Any:
        return await s.call_tool(
            "run_query", {"sql": "select member_key, n_products from dim_member", "limit": 5}
        )

    result = _run(env, "analyst", work)
    assert result.is_error is False
    assert result.structured_content == {
        "columns": ["member_key", "n_products"],
        "rows": [["k1", 1]],
        "row_count": 1,
    }
    (event,) = env.audit("query_executed")
    assert (event["principal"], event["role"]) == (PRINCIPAL, "analyst")


def test_an_analyst_asking_for_health_notes_gets_an_error_and_a_denied_event(env: Env) -> None:
    async def work(s: ClientSession) -> Any:
        return await s.call_tool("run_query", {"sql": "select health_notes from dim_member"})

    result = _run(env, "analyst", work)
    assert result.is_error is True
    assert "query denied" in result.content[0].text
    assert "Chronic" not in result.content[0].text
    assert env.audit("query_executed") == []
    (event,) = env.audit("query_denied")
    assert event["principal"] == PRINCIPAL and event["role"] == "analyst"
    assert event["sensitive_columns"] == ["health_notes"] and event["touched_sensitive"] is True


def test_the_privacy_officer_can_read_it_and_the_read_is_recorded(env: Env) -> None:
    async def work(s: ClientSession) -> Any:
        return await s.call_tool("run_query", {"sql": "select health_notes from dim_member"})

    result = _run(env, "privacy_officer", work)
    assert result.is_error is False and result.structured_content["rows"] == [
        ["Chronic lower back pain"]
    ]
    (event,) = env.audit("query_executed")
    assert event["role"] == "privacy_officer" and event["touched_sensitive"] is True


def test_a_model_cannot_change_its_role_through_a_tool_argument(env: Env) -> None:
    async def work(s: ClientSession) -> Any:
        try:
            return await s.call_tool(
                "run_query",
                {"sql": "select health_notes from dim_member", "role": "privacy_officer"},
            )
        except Exception as err:  # the SDK may reject unexpected arguments outright
            return err

    result = _run(env, "analyst", work)
    assert isinstance(result, Exception) or result.is_error is True
    assert env.audit("query_executed") == []
    assert all(e["role"] == "analyst" for e in env.audit("query_denied"))


@pytest.mark.parametrize(
    ("role", "principal"),
    [(None, "someone"), ("analyst", None), ("root", "someone"), ("", ""), ("analyst", "  ")],
)
def test_the_server_refuses_to_start_without_a_valid_role_and_principal(
    env: Env, role: str | None, principal: str | None
) -> None:
    catalog = _catalog(env)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
    if role is not None:
        environment["PLATFORM_ROLE"] = role
    if principal is not None:
        environment["PLATFORM_PRINCIPAL"] = principal
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "cross_product_platform.cli",
            "serve-mcp",
            "--allowlist",
            str(env.allowlist_path),
            "--warehouse-dir",
            str(env.warehouse_dir),
            "--audit-root",
            str(env.audit_root),
            "--catalog-dir",
            str(catalog),
        ],  # fmt: skip
        env=environment,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert done.returncode == 2
    assert done.stderr.startswith("serve-mcp:")
    assert done.stdout == ""


def test_the_server_refuses_to_start_without_a_role_catalog(env: Env) -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "cross_product_platform.cli",
            "serve-mcp",
            "--allowlist",
            str(env.allowlist_path),
            "--warehouse-dir",
            str(env.warehouse_dir),
            "--audit-root",
            str(env.audit_root),
            "--catalog-dir",
            str(env.root / "nowhere"),
        ],  # fmt: skip
        env={**os.environ, "PLATFORM_ROLE": "analyst", "PLATFORM_PRINCIPAL": "x"},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert done.returncode == 2 and "no catalog for role" in done.stderr


def test_tools_in_process_cover_the_server_code(env: Env) -> None:
    """Same tools without a subprocess (so coverage sees them); the round trip is tested above."""
    from cross_product_platform.mcp_server import build_server, serve_from_env

    catalog = _catalog(env)
    server = build_server(
        "analyst", "in-process", allowlist_path=env.allowlist_path,
        warehouse_dir=env.warehouse_dir, audit_root=env.audit_root, catalog_dir=catalog,
    )  # fmt: skip

    async def call(name: str, arguments: dict[str, Any]) -> Any:
        return await server.call_tool(name, arguments)

    found = asyncio.run(call("search_tables", {"query": "zzz-nothing"}))
    assert found.structured_content == {"tables": []}
    latest = asyncio.run(call("get_metric", {"name": "active_members"}))
    assert latest.structured_content["latest"][0]["value"] == 7
    rows = asyncio.run(call("run_query", {"sql": "select period_month from metric_active_members"}))
    assert rows.structured_content["rows"][0] == ["2026-08-01"]
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="query denied"):
        asyncio.run(call("run_query", {"sql": "select * from fct_secret"}))
    with pytest.raises(ToolError, match="unknown metric"):
        asyncio.run(call("get_metric", {"name": "Bad Name"}))
    with pytest.raises(Exception, match="PLATFORM_ROLE"):
        serve_from_env(
            allowlist_path=env.allowlist_path, warehouse_dir=env.warehouse_dir,
            audit_root=env.audit_root, catalog_dir=catalog,
        )  # fmt: skip
