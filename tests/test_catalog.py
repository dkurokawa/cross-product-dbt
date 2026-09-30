import json
from pathlib import Path
from typing import Any

import pytest

from cross_product_platform.catalog import (
    build_catalog,
    build_role_catalog,
    render_readme,
    write_catalog,
)
from cross_product_platform.cli import main
from cross_product_platform.metrics.spec import load_spec

REPO = Path(__file__).resolve().parent.parent
SPEC = load_spec(REPO / "metrics" / "metrics.yml")


def manifest() -> dict[str, Any]:
    def node(name: str, schema: str, kind: str = "model", **extra: Any) -> dict[str, Any]:
        return {"resource_type": kind, "name": name, "schema": schema, **extra}

    return {
        "nodes": {
            "model.p.dim_member": node(
                "dim_member",
                "silver",
                description="People.",
                columns={
                    "email": {
                        "description": "Address",
                        "config": {"meta": {"pii": "direct", "mask": "email_domain"}},
                    },
                    "member_key": {"config": {"meta": {"pii": "quasi"}}},
                },
                depends_on={"nodes": ["source.p.a.member_registered"]},
            ),
            "model.p.acc_analyst__dim_member": node(
                "acc_analyst__dim_member", "access", depends_on={"nodes": ["model.p.dim_member"]}
            ),
            "model.p.metric_active_members": node("metric_active_members", "gold"),
            "model.p.acc_analyst__metric_active_members": node(
                "acc_analyst__metric_active_members",
                "access",
                depends_on={"nodes": ["model.p.metric_active_members"]},
            ),
            "seed.p.dim_plan": node("dim_plan", "silver", "seed"),
            "test.p.t": {"resource_type": "test", "name": "t", "schema": "silver"},
            "model.p.elsewhere": node("elsewhere", "scratch"),
        },
        "sources": {
            "source.p.a.member_registered": {"source_name": "a", "name": "member_registered"}
        },
    }


WAREHOUSE = {
    "nodes": {
        "model.p.dim_member": {
            "columns": {"member_key": {"type": "VARCHAR"}, "EMAIL": {"type": "VARCHAR"}}
        },
        "model.p.acc_analyst__dim_member": {
            "columns": {"member_key": {"type": "VARCHAR"}, "email_domain": {"type": "VARCHAR"}}
        },
    }
}

ALLOWLIST = {
    "roles": {
        "analyst": {
            "tables": {
                "dim_member": {
                    "model": "acc_analyst__dim_member",
                    "columns": ["member_key", "email_domain"],
                    "pii": {"member_key": "quasi", "email_domain": "quasi"},
                },
                "metric_active_members": {
                    "model": "acc_analyst__metric_active_members",
                    "columns": ["period_month", "product", "active_members"],
                    "pii": {},
                },
            }
        },
        "product_b": {"tables": {}},
    }
}


def test_full_catalog_has_tables_columns_pii_lineage_and_metrics() -> None:
    full = build_catalog(manifest(), WAREHOUSE, SPEC)
    names = [t["name"] for t in full["tables"]]
    assert names == [
        "dim_member", "dim_plan", "metric_active_members",
        "acc_analyst__dim_member", "acc_analyst__metric_active_members",
    ]  # fmt: skip
    person = full["tables"][0]
    assert person["upstream"] == ["a.member_registered"]
    assert person["downstream"] == ["acc_analyst__dim_member"]
    email = next(c for c in person["columns"] if c["name"].lower() == "email")
    assert (
        email["pii"] == "direct" and email["mask"] == "email_domain" and email["type"] == "VARCHAR"
    )
    assert {m["name"] for m in full["metrics"]} >= {"active_members", "net_revenue"}
    variants = next(m for m in full["metrics"] if m["name"] == "active_members")["known_variants"]
    assert {v["reported_as"] for v in variants} >= {"active_users", "seats_under_contract"}
    assert next(v for v in variants if v["product"] == "E")["comparable"] is False


def test_role_catalog_only_lists_that_roles_tables_and_metrics() -> None:
    full = build_catalog(manifest(), WAREHOUSE, SPEC)
    analyst = build_role_catalog("analyst", full, ALLOWLIST)
    assert [t["name"] for t in analyst["tables"]] == ["dim_member", "metric_active_members"]
    assert [c["name"] for c in analyst["tables"][0]["columns"]] == ["member_key", "email_domain"]
    assert analyst["tables"][0]["description"] == "People."
    assert [m["name"] for m in analyst["metrics"]] == ["active_members"]
    assert "email" not in [c["name"] for c in analyst["tables"][0]["columns"]]
    empty = build_role_catalog("product_b", full, ALLOWLIST)
    assert empty["tables"] == [] and empty["metrics"] == []


def test_readme_and_files_are_deterministic(tmp_path: Path) -> None:
    full = build_catalog(manifest(), WAREHOUSE, SPEC)
    first = write_catalog(tmp_path / "a", full, ALLOWLIST)
    second = write_catalog(tmp_path / "b", build_catalog(manifest(), WAREHOUSE, SPEC), ALLOWLIST)
    assert [p.name for p in first] == [
        "catalog.json",
        "README.md",
        "analyst.json",
        "product_b.json",
    ]
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()
    text = render_readme(full)
    assert (
        "### dim_member" in text
        and "## Metrics" in text
        and "Upstream: a.member_registered" in text
    )


def test_cli_catalog_build(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps(manifest()), encoding="utf-8")
    (tmp_path / "catalog.json").write_text(json.dumps(WAREHOUSE), encoding="utf-8")
    (tmp_path / "allow.json").write_text(json.dumps(ALLOWLIST), encoding="utf-8")
    args = [
        "catalog", "build", "--manifest", str(tmp_path / "manifest.json"),
        "--catalog", str(tmp_path / "catalog.json"), "--allowlist", str(tmp_path / "allow.json"),
        "--out-dir", str(tmp_path / "out"),
    ]  # fmt: skip
    assert main(args) == 0
    assert "5 tables" in capsys.readouterr().out
    assert (tmp_path / "out" / "roles" / "analyst.json").exists()
    assert main([*args[:3], str(tmp_path / "missing.json"), *args[4:]]) == 2
