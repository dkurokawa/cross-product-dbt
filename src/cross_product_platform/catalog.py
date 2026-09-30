"""``platform catalog build``: a data catalog generated from dbt's own artifacts (design §7).

Inputs are ``target/manifest.json`` (models, descriptions, ``meta.pii``, lineage) and
``target/catalog.json`` (column types from the warehouse) plus ``metrics/metrics.yml``.
Outputs, all under one directory (a build artifact, not committed):

* ``catalog.json`` and ``README.md`` - every table, column, description, pii class, upstream and
  downstream table, and the metric definitions with their known variants;
* ``roles/<role>.json`` - the same information restricted to what that role may read: only its
  access tables, under the plain names it sees, and only the metrics it can query.

The output is deterministic (sorted, no timestamps), so two builds from the same artifacts are
byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from .metrics.spec import MetricsSpec

LAYERS: Final = ("bronze", "silver", "gold", "access")


def _short(uid: str, manifest: dict[str, Any]) -> str:
    node = manifest["nodes"].get(uid) or manifest.get("sources", {}).get(uid)
    if node is None:
        return uid
    if uid.startswith("source."):
        return f"{node['source_name']}.{node['name']}"
    return str(node["name"])


def _column(name: str, declared: dict[str, Any], types: dict[str, str]) -> dict[str, Any]:
    meta = (declared.get("config") or {}).get("meta") or declared.get("meta") or {}
    entry: dict[str, Any] = {
        "name": name,
        "type": types.get(name.lower(), ""),
        "description": declared.get("description", ""),
        "pii": meta.get("pii", ""),
    }
    if meta.get("mask"):
        entry["mask"] = meta["mask"]
    return entry


def build_catalog(
    manifest: dict[str, Any], warehouse_catalog: dict[str, Any], spec: MetricsSpec
) -> dict[str, Any]:
    """The full catalog as a JSON-serializable dict."""
    downstream: dict[str, set[str]] = {}
    for uid, node in manifest["nodes"].items():
        for parent in node.get("depends_on", {}).get("nodes", []):
            downstream.setdefault(parent, set()).add(_short(uid, manifest))
    tables: list[dict[str, Any]] = []
    for uid, node in manifest["nodes"].items():
        if node.get("resource_type") not in {"model", "seed"} or node.get("schema") not in LAYERS:
            continue
        found = warehouse_catalog.get("nodes", {}).get(uid, {}).get("columns", {})
        types = {str(k).lower(): str(v.get("type", "")) for k, v in found.items()}
        declared = {k.lower(): v for k, v in node.get("columns", {}).items()}
        names = [str(k) for k in found] or list(node.get("columns", {}))
        tables.append(
            {
                "name": node["name"],
                "schema": node["schema"],
                "kind": node["resource_type"],
                "description": node.get("description", ""),
                "columns": [_column(n, declared.get(n.lower(), {}), types) for n in names],
                "upstream": sorted(
                    {_short(p, manifest) for p in node.get("depends_on", {}).get("nodes", [])}
                ),
                "downstream": sorted(downstream.get(uid, set())),
            }
        )
    tables.sort(key=lambda t: (LAYERS.index(t["schema"]), t["name"]))
    return {"tables": tables, "metrics": [_metric(m) for m in spec.metrics]}


def _metric(metric: Any) -> dict[str, Any]:
    return {
        "name": metric.name,
        "description": metric.description,
        "owner": metric.owner,
        "kind": metric.kind,
        "grain": list(metric.grain),
        "definition": metric.definition,
        "model": f"metric_{metric.name}",
        "reconciliation_model": f"recon_{metric.name}",
        "known_variants": [
            {
                "product": v.product,
                "reported_as": v.reported_as,
                "definition": v.definition,
                "comparable": v.comparable,
                "expected_direction": v.expected_direction if v.comparable else None,
                "threshold_pct": v.threshold_pct if v.comparable else None,
                "note": v.note,
            }
            for v in metric.known_variants
        ],
    }


def build_role_catalog(
    role: str, full: dict[str, Any], allowlist: dict[str, Any]
) -> dict[str, Any]:
    """The catalog of one role: only the tables in its allowlist, under the names it sees."""
    granted = allowlist["roles"][role]["tables"]
    by_name = {t["name"]: t for t in full["tables"]}
    tables: list[dict[str, Any]] = []
    for name, spec in granted.items():
        model = by_name.get(spec["model"], {})
        base = by_name.get(name, {})
        types = {c["name"]: c["type"] for c in model.get("columns", [])}
        described = {c["name"]: c["description"] for c in base.get("columns", [])}
        tables.append(
            {
                "name": name,
                "description": base.get("description", ""),
                "columns": [
                    {
                        "name": c,
                        "type": types.get(c, ""),
                        "description": described.get(c, ""),
                        "pii": spec.get("pii", {}).get(c, ""),
                    }
                    for c in spec["columns"]
                ],
            }
        )
    tables.sort(key=lambda t: t["name"])
    visible = {t["name"] for t in tables}
    metrics = [m for m in full["metrics"] if m["model"] in visible]
    return {"role": role, "tables": tables, "metrics": metrics}


def render_readme(full: dict[str, Any]) -> str:
    """A human-readable version of the full catalog."""
    lines = [
        "# Data catalog",
        "",
        "Generated by `platform catalog build` from dbt's artifacts.",
        "",
    ]
    for layer in LAYERS:
        members = [t for t in full["tables"] if t["schema"] == layer]
        if not members:
            continue
        lines += [f"## {layer}", ""]
        for t in members:
            lines += [f"### {t['name']}", "", t["description"] or "(no description)", ""]
            if t["upstream"]:
                lines.append(f"Upstream: {', '.join(t['upstream'])}  ")
            if t["downstream"]:
                lines.append(f"Downstream: {', '.join(t['downstream'])}")
            lines += ["", "| column | type | pii | description |", "|---|---|---|---|"]
            for c in t["columns"]:
                lines.append(f"| {c['name']} | {c['type']} | {c['pii']} | {c['description']} |")
            lines.append("")
    lines += ["## Metrics", ""]
    for m in full["metrics"]:
        lines += [f"### {m['name']}", "", m["description"], ""]
        lines += ["| product | reported as | comparable | note |", "|---|---|---|---|"]
        for v in m["known_variants"]:
            lines.append(
                f"| {v['product']} | {v['reported_as']} | {'yes' if v['comparable'] else 'no'} | "
                f"{v['note'] or v['definition']} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def write_catalog(out_dir: Path, full: dict[str, Any], allowlist: dict[str, Any]) -> list[Path]:
    """Write ``catalog.json``, ``README.md`` and one JSON per role; returns the paths."""
    written: list[Path] = []

    def dump(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)

    dump(out_dir / "catalog.json", full)
    readme = out_dir / "README.md"
    readme.write_text(render_readme(full), encoding="utf-8")
    written.append(readme)
    for role in allowlist["roles"]:
        dump(out_dir / "roles" / f"{role}.json", build_role_catalog(role, full, allowlist))
    return written
