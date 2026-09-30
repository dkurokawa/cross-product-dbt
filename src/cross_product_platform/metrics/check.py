"""``platform metrics check``: three guards around the metric definitions (design F1, F2).

1. the generated files are up to date (regenerate and compare: zero difference);
2. no non-generated model creates a column named like a metric or like a product's own KPI
   (a same-name-different-definition metric hiding in a hand-written model);
3. every KPI a product actually reports is a known_variant of some metric.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .codegen import METRICS_DIR, RECON_DIR, generate_files
from .spec import MetricsSpec

_GENERATED_PREFIXES = (f"{METRICS_DIR}/", f"{RECON_DIR}/")


def write_files(files: dict[str, str], repo_root: Path) -> None:
    """Write generated files below ``repo_root`` and delete stale generated ones."""
    for rel, content in files.items():
        target = repo_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for stale in _stale(files, repo_root):
        (repo_root / stale).unlink()


def _stale(files: dict[str, str], repo_root: Path) -> list[str]:
    found: list[str] = []
    for prefix in _GENERATED_PREFIXES:
        directory = repo_root / prefix
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                rel = f"{prefix}{path.name}"
                if path.is_file() and rel not in files:
                    found.append(rel)
    return found


def check_generated(spec: MetricsSpec, repo_root: Path) -> list[str]:
    """Check 1: regenerate in memory and compare with what is on disk."""
    problems: list[str] = []
    files = generate_files(spec)
    for rel, expected in files.items():
        path = repo_root / rel
        if not path.exists():
            problems.append(f"generated file is missing: {rel} (run `platform metrics generate`)")
        elif path.read_text(encoding="utf-8") != expected:
            problems.append(f"generated file is out of date or was edited: {rel}")
    problems += [f"stale generated file not in the spec: {rel}" for rel in _stale(files, repo_root)]
    return problems


def check_manifest(
    spec: MetricsSpec, manifest: dict[str, Any], catalog: dict[str, Any] | None = None
) -> list[str]:
    """Check 2: non-generated models must not produce a guarded column name."""
    guarded = {n.lower() for n in spec.guarded_column_names()}
    problems: list[str] = []
    for uid, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "model":
            continue
        if (
            str(node.get("original_file_path", ""))
            .replace("\\", "/")
            .startswith(tuple(p.removeprefix("dbt/") for p in _GENERATED_PREFIXES))
        ):
            continue
        columns = {str(c).lower() for c in node.get("columns", {})}
        if catalog is not None:
            entry = catalog.get("nodes", {}).get(uid, {})
            columns |= {str(c).lower() for c in entry.get("columns", {})}
        for column in sorted(columns & guarded):
            problems.append(f"{node.get('name', uid)} creates column {column!r}, a metric name")
    return problems


def check_reported(spec: MetricsSpec, lake_root: Path) -> list[str]:
    """Check 3: every landed reported KPI must be a known variant."""
    files = sorted((lake_root / "reported_kpis").glob("*/kpis.parquet"))
    if not files:
        return [f"no reported_kpis found below {lake_root}"]
    frame = pd.concat([pd.read_parquet(f) for f in files])
    seen = {(str(p), str(r)) for p, r in zip(frame["product"], frame["reported_as"], strict=True)}
    known = spec.reported_names()
    return [
        f"product {p} reports {r!r}, which is not a known_variant of any metric"
        for p, r in sorted(seen - known)
    ]


def load_json(path: Path) -> dict[str, Any]:
    """Read a JSON artifact of dbt."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data
