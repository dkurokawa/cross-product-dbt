"""Proves the row_count_anomaly test does its job (design section 4).

1. On the clean demo lake (``build/lake``, made by ``make demo``) the test must not warn.
2. On a second lake generated with a one-day gap in product A, the test must warn, and the
   stored failing rows must name exactly the injected day.

Runs dbt as a subprocess (one warehouse file per lake) and reads dbt's own artifacts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
GAP_DAY = date(2026, 3, 10)
MODELS = ["brz_a_app_opened", "brz_a_workout_completed"]


def _dbt(lake: Path, warehouse: Path, *extra: str) -> None:
    env = {
        **os.environ,
        "PLATFORM_LANDING_ROOT": str(lake),
        "PLATFORM_WAREHOUSE": str(warehouse),
    }
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "dbt.cli.main", "build",
        "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt"),
        "--select", *MODELS, *extra,
    ]  # fmt: skip
    subprocess.run(cmd, cwd=ROOT, env=env, check=False, capture_output=True)


def _anomaly_statuses() -> dict[str, str]:
    results = json.loads((ROOT / "dbt" / "target" / "run_results.json").read_text("utf-8"))
    return {
        r["unique_id"]: r["status"]
        for r in results["results"]
        if "row_count_anomaly" in r["unique_id"]
    }


def main() -> int:
    """Return 0 when the clean lake is quiet and the gap lake is detected."""
    clean_lake = ROOT / "build" / "lake"
    if not clean_lake.is_dir():
        print("run `make demo` first: build/lake is missing", file=sys.stderr)
        return 2

    _dbt(clean_lake, ROOT / "build" / "warehouse" / "anomaly_clean.duckdb")
    clean = _anomaly_statuses()
    if len(clean) != len(MODELS) or any(s != "pass" for s in clean.values()):
        print(f"clean lake should not warn, got {clean}", file=sys.stderr)
        return 1
    print(f"clean lake: {len(clean)} row_count_anomaly tests pass")

    gap_lake = ROOT / "build" / "lake_gap"
    subprocess.run(
        [sys.executable, "-m", "cross_product_platform.cli", "ingest", "--root", str(gap_lake),
         "--overwrite", "--gap-day", GAP_DAY.isoformat()],
        cwd=ROOT, check=True, capture_output=True,
    )  # fmt: skip
    gap_warehouse = ROOT / "build" / "warehouse" / "anomaly_gap.duckdb"
    _dbt(gap_lake, gap_warehouse, "--store-failures")
    gapped = _anomaly_statuses()
    if len(gapped) != len(MODELS) or any(s != "warn" for s in gapped.values()):
        print(f"gap lake should warn on every model, got {gapped}", file=sys.stderr)
        return 1

    con = duckdb.connect(str(gap_warehouse), read_only=True)
    tables = con.execute(
        "select table_name from information_schema.tables "
        "where table_schema = 'dbt_test__audit' and table_name like '%row_count_anomaly%'"
    ).fetchall()
    days: set[date] = set()
    for (name,) in tables:
        days |= {r[0] for r in con.execute(f'select day from dbt_test__audit."{name}"').fetchall()}
    if days != {GAP_DAY}:
        print(f"expected exactly {GAP_DAY} to be flagged, got {sorted(days)}", file=sys.stderr)
        return 1
    print(f"gap lake: both tests warn, flagged day = {GAP_DAY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
