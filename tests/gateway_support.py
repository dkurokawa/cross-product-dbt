"""A tiny synthetic warehouse, allowlist and role databases for the gateway tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from cross_product_platform.gateway import Gateway
from cross_product_platform.roles import build_role_databases, load_allowlist

ALLOWLIST: dict[str, Any] = {
    "sensitive_columns": ["health_notes"],
    "roles": {
        "analyst": {
            "tables": {
                "dim_member": {
                    "model": "acc_analyst__dim_member",
                    "columns": ["member_key", "email_domain", "n_products"],
                },
                "fct_payment": {
                    "model": "acc_analyst__fct_payment",
                    "columns": ["payment_id", "member_key", "amount_tax_incl"],
                },
                "metric_active_members": {
                    "model": "acc_analyst__metric_active_members",
                    "columns": ["period_month", "product", "active_members"],
                },
            }
        },
        "product_a": {
            "tables": {
                "dim_member": {
                    "model": "acc_product_a__dim_member",
                    "columns": ["member_key", "email_domain", "n_products"],
                }
            }
        },
        "privacy_officer": {
            "tables": {
                "dim_member": {
                    "model": "acc_privacy_officer__dim_member",
                    "columns": ["member_key", "health_notes", "n_products"],
                },
                "audit_access_log": {
                    "model": "acc_privacy_officer__audit_access_log",
                    "columns": ["principal", "role", "outcome", "touched_sensitive"],
                },
            }
        },
    },
}


@dataclass(frozen=True)
class Env:
    """Everything a gateway test needs."""

    root: Path
    allowlist_path: Path
    warehouse_dir: Path
    audit_root: Path

    def gateway(self, role: str, principal: str = "tester") -> Gateway:
        return Gateway(
            role,
            principal,
            allowlist_path=self.allowlist_path,
            warehouse_dir=self.warehouse_dir,
            audit_root=self.audit_root,
        )

    def audit(self, event_type: str) -> list[dict[str, Any]]:
        files = list((self.audit_root / event_type).glob("dt=*/part-*.parquet"))
        if not files:
            return []
        con = duckdb.connect()
        cursor = con.execute(
            f"select * from read_parquet({[str(f) for f in files]!r}, union_by_name=true) "
            "order by occurred_at"
        )
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def make_env(root: Path) -> Env:
    """A main warehouse with access tables, the allowlist file and one database per role."""
    warehouse = root / "platform.duckdb"
    con = duckdb.connect(str(warehouse))
    con.execute("create schema access")
    for model, cols in (
        (
            "acc_analyst__dim_member",
            "select 'k1' member_key, 'example.jp' email_domain, 1 n_products",
        ),
        (
            "acc_analyst__fct_payment",
            "select 'p1' payment_id, 'k1' member_key, 1100 amount_tax_incl",
        ),
        (
            "acc_analyst__metric_active_members",
            "select * from (values (date '2026-08-01', 'ALL', 10), (date '2026-09-01', 'ALL', 12),"
            " (date '2026-09-01', 'A', 7)) t(period_month, product, active_members)",
        ),
        (
            "acc_product_a__dim_member",
            "select 'k1' member_key, 'example.jp' email_domain, 1 n_products",
        ),
        (
            "acc_privacy_officer__dim_member",
            "select 'k1' member_key, 'Chronic lower back pain' health_notes, 1 n_products",
        ),
    ):
        con.execute(f"create table access.{model} as {cols}")
    con.execute(
        "create table access.acc_privacy_officer__audit_access_log as "
        "select 'nosy' as principal, 'analyst' as role, 'denied' as outcome, "
        "true as touched_sensitive"
    )
    con.execute("create schema silver")
    con.execute("create table silver.dim_member as select 'raw-secret@example.com' email")
    con.close()
    allowlist_path = root / "allowlist.json"
    allowlist_path.write_text(json.dumps(ALLOWLIST), encoding="utf-8")
    directory = root / "wh"
    build_role_databases(load_allowlist(allowlist_path), warehouse, directory)
    return Env(root, allowlist_path, directory, root / "audit")
