"""The MCP server (stdio) that hands the catalog and queries to an LLM (design §7, F8).

Four tools, all read-only: ``search_tables``, ``describe_table``, ``get_metric`` and
``run_query``. Queries go through the gateway, so they are checked, limited and audited exactly
like any other call.

**The role and the principal are fixed at start-up from the environment**
(``PLATFORM_ROLE``, ``PLATFORM_PRINCIPAL``); no tool has an argument that could change them, so a
model cannot promote itself. A missing or unknown role stops the server from starting. The
principal is self-declared: there is no authentication in this local template.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .gateway import MAX_LIMIT, Gateway, GatewayError, QueryDeniedError

ROLE_ENV: Final = "PLATFORM_ROLE"
PRINCIPAL_ENV: Final = "PLATFORM_PRINCIPAL"
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def load_role_catalog(catalog_dir: Path, role: str) -> dict[str, Any]:
    """The catalog restricted to ``role`` (built by ``platform catalog build``)."""
    path = catalog_dir / "roles" / f"{role}.json"
    if not path.exists():
        raise GatewayError(f"no catalog for role {role} at {path}; run `make catalog`")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GatewayError(f"{path} is not a role catalog")
    return data


def build_server(
    role: str,
    principal: str,
    *,
    allowlist_path: Path,
    warehouse_dir: Path,
    audit_root: Path,
    catalog_dir: Path,
) -> MCPServer:
    """Create the server for one fixed role and principal (raises ``GatewayError`` if unusable)."""
    gateway = Gateway(
        role,
        principal,
        allowlist_path=allowlist_path,
        warehouse_dir=warehouse_dir,
        audit_root=audit_root,
    )
    catalog = load_role_catalog(catalog_dir, role)
    tables: dict[str, dict[str, Any]] = {t["name"]: t for t in catalog["tables"]}
    metrics: dict[str, dict[str, Any]] = {m["name"]: m for m in catalog["metrics"]}
    server = MCPServer(
        "cross-product-platform",
        instructions=(
            f"Read-only access to the cross-product data platform as role '{role}'. "
            "Use search_tables and describe_table first, then run_query."
        ),
    )

    @server.tool()
    def search_tables(query: str) -> dict[str, Any]:
        """Find tables by words in their name, description or column names."""
        words = [w.removesuffix("s") for w in re.split(r"\W+", query.lower()) if w]
        found: list[dict[str, Any]] = []
        for name, table in tables.items():
            score = 0
            matched: list[str] = []
            for word in words:
                score += 3 * (word in name.lower())
                score += 2 * (word in table["description"].lower())
                hits = [c["name"] for c in table["columns"] if word in c["name"].lower()]
                score += len(hits)
                matched += hits
            if score:
                found.append(
                    {
                        "name": name,
                        "description": table["description"],
                        "matching_columns": sorted(set(matched)),
                        "score": score,
                    }
                )
        found.sort(key=lambda t: (-t["score"], t["name"]))
        return {"tables": found}

    @server.tool()
    def describe_table(name: str) -> dict[str, Any]:
        """Columns (type, description, personal-data class) of one table you may read."""
        table = tables.get(name)
        if table is None:
            raise ToolError(f"unknown table {name!r} for this role")
        return table

    @server.tool()
    def get_metric(name: str) -> dict[str, Any]:
        """A metric's definition, the products' own variants of it, and its latest value."""
        metric = metrics.get(name)
        if metric is None or not _NAME.match(name):
            raise ToolError(f"unknown metric {name!r} for this role")
        table = metric["model"]
        sql = (
            f"select period_month, product, {name} as value from {table} "
            f"where period_month = (select max(period_month) from {table}) order by product"
        )
        try:
            result = gateway.query(sql)
        except QueryDeniedError as err:
            raise ToolError(f"the latest value could not be read: {err.reason}") from None
        latest = [
            {c: _jsonable(v) for c, v in zip(result.columns, row, strict=True)}
            for row in result.rows
        ]
        return {**metric, "latest": latest}

    @server.tool()
    def run_query(sql: str, limit: int = MAX_LIMIT) -> dict[str, Any]:
        """Run one SELECT on the tables of this role (read-only, at most 1000 rows)."""
        try:
            result = gateway.query(sql, limit)
        except QueryDeniedError as err:
            raise ToolError(f"query denied: {err.reason}") from None
        return {
            "columns": result.columns,
            "rows": [[_jsonable(v) for v in row] for row in result.rows],
            "row_count": result.row_count,
        }

    return server


def serve_from_env(
    *, allowlist_path: Path, warehouse_dir: Path, audit_root: Path, catalog_dir: Path
) -> None:
    """Read the role and principal from the environment and serve over stdio."""
    role = os.environ.get(ROLE_ENV, "").strip()
    principal = os.environ.get(PRINCIPAL_ENV, "").strip()
    if not role or not principal:
        raise GatewayError(f"{ROLE_ENV} and {PRINCIPAL_ENV} must both be set")
    build_server(
        role,
        principal,
        allowlist_path=allowlist_path,
        warehouse_dir=warehouse_dir,
        audit_root=audit_root,
        catalog_dir=catalog_dir,
    ).run()
