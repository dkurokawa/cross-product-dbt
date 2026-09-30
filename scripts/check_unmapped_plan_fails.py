"""Proves an unmapped plan code fails the build instead of passing quietly (design section 4).

Generates a small lake with one C contract whose plan code is not in the ``dim_plan`` seed,
builds the dbt project on it, and requires the ``relationships`` test on ``plan_ref`` to fail.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """Return 0 when the unmapped code is caught by a failing test."""
    lake = ROOT / "build" / "lake_unmapped"
    warehouse = ROOT / "build" / "warehouse" / "unmapped.duckdb"
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "cross_product_platform.cli", "ingest", "--root", str(lake),
         "--overwrite", "--unmapped-plan", "--persons", "600"],
        cwd=ROOT, check=True, capture_output=True,
    )  # fmt: skip
    env = {**os.environ, "PLATFORM_LANDING_ROOT": str(lake), "PLATFORM_WAREHOUSE": str(warehouse)}
    subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "build",
         "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt"),
         "--exclude", "tag:audit"],
        cwd=ROOT, env=env, check=False, capture_output=True,
    )  # fmt: skip
    results = json.loads((ROOT / "dbt" / "target" / "run_results.json").read_text("utf-8"))
    failed = [
        r["unique_id"]
        for r in results["results"]
        if r["status"] in {"fail", "error"} and "relationships" in r["unique_id"]
        and "plan_ref" in r["unique_id"]
    ]  # fmt: skip
    if not failed:
        print("the unmapped plan code was NOT caught", file=sys.stderr)
        return 1
    print(f"unmapped plan code fails the build: {', '.join(sorted(failed))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
