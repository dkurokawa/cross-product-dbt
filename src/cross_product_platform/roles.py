"""Per-role database files (design decision F6, layer 4).

After ``dbt build``, every role gets its own DuckDB file that contains ONLY that role's access
tables, copied as plain tables under the name the role sees (``dim_member``, not
``acc_analyst__dim_member``). The gateway opens only these files, read-only, so a query that
slips past the SQL checks still cannot see a table the role does not have: it does not exist in
the file.

The files are created with an ordinary writable connection here; the main warehouse (which holds
bronze and silver with raw personal data) is opened read-only and only by this build step.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import duckdb

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


def role_db_path(directory: Path, role: str) -> Path:
    """Where the database file of ``role`` lives."""
    if not _IDENT.match(role):
        raise ValueError(f"invalid role name {role!r}")
    return directory / f"access_{role}.duckdb"


def load_allowlist(path: Path) -> dict[str, Any]:
    """Read ``policies/gateway_allowlist.json``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "roles" not in data:
        raise ValueError(f"{path} is not a gateway allowlist")
    return data


def build_role_databases(
    allowlist: dict[str, Any], warehouse: Path, out_dir: Path
) -> dict[str, int]:
    """Create ``access_<role>.duckdb`` for every role; returns ``{role: number of tables}``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    built: dict[str, int] = {}
    for role, body in allowlist["roles"].items():
        target = role_db_path(out_dir, role)
        for stale in (target, target.with_name(target.name + ".wal")):
            stale.unlink(missing_ok=True)
        con = duckdb.connect(str(target))
        try:
            quoted = str(warehouse).replace("'", "''")
            con.execute(f"attach '{quoted}' as src (read_only)")
            for table, spec in body["tables"].items():
                names = [table, spec["model"], *spec["columns"]]
                if not all(_IDENT.match(n) for n in names):
                    raise ValueError(f"unsafe identifier in the allowlist for {role}.{table}")
                columns = ", ".join(f'"{c}"' for c in spec["columns"])
                con.execute(
                    f'create table "{table}" as select {columns} from src.access."{spec["model"]}"'
                )
            con.execute("detach src")
        finally:
            con.close()
        built[role] = len(body["tables"])
    return built
