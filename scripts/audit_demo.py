"""Exercises the gateway on the real role databases and checks the audit log built from it.

``write``  runs five sample queries (allowed, denied by the SQL check, a privacy officer reading
           a sensitive column) so that the audit lake has events;
``check``  reads gold.audit_access_log from the warehouse and requires that the privacy officer's
           read of ``health_notes`` and the analyst's attempt at it are both there.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import duckdb

from cross_product_platform.gateway import Gateway, QueryDeniedError

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = Path(
    os.environ.get("PLATFORM_WAREHOUSE", ROOT / "build" / "warehouse" / "platform.duckdb")
)
WAREHOUSE_DIR = WAREHOUSE.parent
AUDIT_ROOT = Path(os.environ.get("PLATFORM_AUDIT_ROOT", ROOT / "build" / "audit"))

SAMPLES: list[tuple[str, str, str]] = [
    ("analyst", "ada", "select count(*) as members from dim_member"),
    ("analyst", "nosy", "select health_notes from dim_member"),
    (
        "privacy_officer",
        "audrey",
        "select count(*) as with_notes from dim_member where health_notes is not null",
    ),
    ("product_a", "pat", "select * from fct_booking"),
    ("analyst", "mallory", "select * from read_parquet('/etc/hosts')"),
]


def write() -> int:
    """Start from an empty audit lake and run the sample queries."""
    if AUDIT_ROOT.exists():
        shutil.rmtree(AUDIT_ROOT)
    for role, principal, sql in SAMPLES:
        gateway = Gateway(
            role,
            principal,
            allowlist_path=ROOT / "policies" / "gateway_allowlist.json",
            warehouse_dir=WAREHOUSE_DIR,
            audit_root=AUDIT_ROOT,
        )
        try:
            result = gateway.query(sql)
            print(f"{role}/{principal}: {result.row_count} row(s)")
        except QueryDeniedError as err:
            print(f"{role}/{principal}: denied ({err.reason})")
    return 0


def check() -> int:
    """Require the expected rows in gold.audit_access_log."""
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    rows = con.execute(
        "select outcome, principal, role, touched_sensitive, sensitive_columns, deny_reason "
        "from gold.audit_access_log order by occurred_at"
    ).fetchall()
    for row in rows:
        print(" | ".join(str(v) for v in row))
    got = {(r[1], r[0], r[3]) for r in rows}
    expected = {
        ("ada", "executed", False),
        ("nosy", "denied", True),
        ("audrey", "executed", True),
        ("pat", "denied", False),
        ("mallory", "denied", False),
    }
    if len(rows) != len(SAMPLES) or got != expected:
        print(f"audit log does not match the sample queries: {sorted(got)}", file=sys.stderr)
        return 1
    sensitive_readers = con.execute(
        "select principal from gold.audit_access_log "
        "where touched_sensitive and outcome = 'executed'"
    ).fetchall()
    if sensitive_readers != [("audrey",)]:
        print(f"unexpected readers of sensitive columns: {sensitive_readers}", file=sys.stderr)
        return 1
    print("audit log ok: the privacy officer's read and the analyst's attempt are both recorded")
    return 0


if __name__ == "__main__":
    sys.exit(write() if sys.argv[1:] == ["write"] else check())
