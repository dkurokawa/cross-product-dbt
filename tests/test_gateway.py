from pathlib import Path
from typing import Any

import duckdb
import pytest

from cross_product_platform.cli import main
from cross_product_platform.gateway import (
    MAX_LIMIT,
    Gateway,
    GatewayError,
    QueryDeniedError,
    analyze,
    execute_readonly,
)
from cross_product_platform.roles import build_role_databases, load_allowlist, role_db_path

from .gateway_support import ALLOWLIST, Env, make_env


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return make_env(tmp_path)


TABLES = {"dim_member": ["member_key", "email_domain", "n_products"], "fct_payment": ["payment_id"]}

# (query, why it must be denied by the sqlglot layer)
BLOCKED = [
    ("select * from read_parquet('/tmp/x.parquet')", "table function"),
    ("select * from read_csv_auto('/etc/hosts')", "table function"),
    ("select * from 'x.parquet'", "replacement scan"),
    ("select glob('*')", "file function"),
    ("select read_text('/etc/hosts')", "file function"),
    ("select * from glob('/*')", "table function"),
    ("select * from query_table('dim_member')", "table function"),
    ("select * from information_schema.tables", "qualified name"),
    ("select * from main.dim_member", "qualified name"),
    ("attach '/tmp/x.db' as x", "ATTACH"),
    ("install httpfs", "INSTALL"),
    ("load httpfs", "LOAD"),
    ("copy dim_member to '/tmp/x.csv'", "COPY"),
    ("pragma database_list", "PRAGMA"),
    ("set enable_external_access = true", "SET"),
    ("delete from dim_member", "DML"),
    ("drop table dim_member", "DDL"),
    ("select 1; select 2", "two statements"),
    ("select * from silver.dim_member", "another schema"),
    ("select * from fct_payment_secret", "table not allowed"),
    ("select health_notes from dim_member", "column the role cannot see"),
    ("select ((( from", "unparseable"),
]


@pytest.mark.parametrize(("sql", "why"), BLOCKED)
def test_sqlglot_blocked_queries_are_denied_and_audited(env: Env, sql: str, why: str) -> None:
    gateway = env.gateway("analyst", "mallory")
    with pytest.raises(QueryDeniedError):
        gateway.query(sql)
    denied = env.audit("query_denied")
    assert len(denied) == 1, why
    assert env.audit("query_executed") == []
    event = denied[0]
    assert event["principal"] == "mallory" and event["role"] == "analyst"
    assert event["deny_reason"] and len(event["sql_hash"]) == 64


def test_exactly_one_audit_event_per_call(env: Env) -> None:
    gateway = env.gateway("analyst")
    gateway.query("select member_key from dim_member")
    with pytest.raises(QueryDeniedError):
        gateway.query("select * from other")
    gateway.query("select payment_id from fct_payment")
    assert len(env.audit("query_executed")) == 2
    assert len(env.audit("query_denied")) == 1


def test_an_allowed_query_returns_rows_and_records_tables_and_columns(env: Env) -> None:
    result = env.gateway("analyst", "ann").query(
        "select m.member_key, p.amount_tax_incl from dim_member m "
        "join fct_payment p using (member_key) where m.email_domain = 'example.jp'"
    )
    assert result.columns == ["member_key", "amount_tax_incl"]
    assert result.rows == [("k1", 1100)] and result.row_count == 1
    (event,) = env.audit("query_executed")
    assert event["referenced_tables"] == ["dim_member", "fct_payment"]
    assert "dim_member.email_domain" in event["referenced_columns"]
    assert event["row_count"] == 1 and event["touched_sensitive"] is False
    assert event["principal"] == "ann"


def test_literals_are_not_written_to_the_audit_log(env: Env) -> None:
    env.gateway("analyst").query(
        "select member_key from dim_member where email_domain = 'secret.example'"
    )
    (event,) = env.audit("query_executed")
    assert "secret.example" not in event["sql_normalized"]
    assert "?" in event["sql_normalized"]


def test_same_query_with_other_literals_has_the_same_hash(env: Env) -> None:
    a = analyze("select member_key from dim_member where n_products = 1", TABLES, set())
    b = analyze("select member_key from dim_member where n_products = 2", TABLES, set())
    assert a.sql_hash == b.sql_hash and a.normalized == b.normalized


def test_the_row_limit_is_enforced_and_capped(env: Env) -> None:
    gateway = env.gateway("analyst")
    with pytest.raises(QueryDeniedError, match="limit must be"):
        gateway.query("select member_key from dim_member", limit=MAX_LIMIT + 1)
    with pytest.raises(QueryDeniedError, match="limit must be"):
        gateway.query("select member_key from dim_member", limit=0)
    analysis = analyze("select member_key from dim_member", TABLES, set(), limit=7)
    assert analysis.executable.endswith(") as q limit 7")


def test_a_users_own_limit_below_the_cap_still_applies(tmp_path: Path) -> None:
    root = tmp_path
    warehouse = root / "w.duckdb"
    con = duckdb.connect(str(warehouse))
    con.execute("create schema access")
    con.execute("create table access.acc_analyst__t as select range as n from range(3000)")
    con.close()
    allow = {
        "sensitive_columns": [],
        "roles": {"analyst": {"tables": {"t": {"model": "acc_analyst__t", "columns": ["n"]}}}},
    }
    build_role_databases(allow, warehouse, root / "wh")
    db = role_db_path(root / "wh", "analyst")
    assert (
        execute_readonly(db, "select * from (select n from t) as q limit 1000").rows.__len__()
        == 1000
    )
    analysis = analyze("select n from t limit 5", {"t": ["n"]}, set(), limit=1000)
    assert len(execute_readonly(db, analysis.executable).rows) == 5
    analysis = analyze("select n from t", {"t": ["n"]}, set(), limit=1000)
    assert len(execute_readonly(db, analysis.executable).rows) == 1000


def test_sensitive_columns_are_flagged_even_when_the_role_cannot_read_them(env: Env) -> None:
    with pytest.raises(QueryDeniedError):
        env.gateway("analyst", "nosy").query("select health_notes from dim_member")
    (event,) = env.audit("query_denied")
    assert event["sensitive_columns"] == ["health_notes"] and event["touched_sensitive"] is True
    assert event["referenced_columns"] == ["health_notes"]


def test_the_privacy_officer_can_read_sensitive_columns_and_it_is_recorded(env: Env) -> None:
    result = env.gateway("privacy_officer", "auditor").query("select health_notes from dim_member")
    assert result.rows == [("Chronic lower back pain",)]
    (event,) = env.audit("query_executed")
    assert event["touched_sensitive"] is True
    assert event["sensitive_columns"] == ["health_notes"]
    assert event["referenced_columns"] == ["dim_member.health_notes"]


# ---- layer independence: the database layer alone ----

# ---- layer independence: the database layer alone ----

DB_LAYER = [
    ("select * from read_parquet('/etc/hosts')", "file read"),
    ("select * from read_csv_auto('/etc/hosts')", "file read"),
    ("select * from glob('/*')", "file listing"),
    ("attach '/tmp/gateway-test-other.db' as other", "ATTACH"),
    ("install httpfs", "INSTALL"),
    ("load httpfs", "LOAD"),
    ("copy (select 1) to '/tmp/gateway-test-out.csv'", "COPY TO"),
    ("set enable_external_access = true", "re-enabling external access"),
    ("set lock_configuration = false", "unlocking the configuration"),
    ("create table t as select 1", "writing"),
    ("select * from fct_payment", "another role's table"),
    ("select health_notes from dim_member", "a column that is not in the file"),
]


@pytest.mark.parametrize(("sql", "why"), DB_LAYER)
def test_db_layer_holds_when_sqlglot_is_bypassed(env: Env, sql: str, why: str) -> None:
    """Call the execution layer directly: the database settings and the file contents hold alone.

    The product_a database has no ``fct_payment`` table and no ``health_notes`` column, and it is
    opened read-only with external access off and the configuration locked.
    """
    db = role_db_path(env.warehouse_dir, "product_a")
    with pytest.raises(duckdb.Error):
        execute_readonly(db, sql)
    assert not Path("/tmp/gateway-test-out.csv").exists(), why
    assert not Path("/tmp/gateway-test-other.db").exists(), why


def test_db_layer_only_holds_what_the_role_was_given(env: Env) -> None:
    db = role_db_path(env.warehouse_dir, "product_a")
    assert execute_readonly(db, "select member_key from dim_member").rows == [("k1",)]
    tables = execute_readonly(db, "select table_name from information_schema.tables").rows
    assert tables == [("dim_member",)]
    raw = execute_readonly(db, "select column_name from information_schema.columns").rows
    assert {c for (c,) in raw} == {"member_key", "email_domain", "n_products"}
    # the raw warehouse schemas are not in the file
    with pytest.raises(duckdb.Error):
        execute_readonly(db, "select * from silver.dim_member")


def test_the_gateway_never_opens_the_main_warehouse(env: Env) -> None:
    gateway = env.gateway("analyst")
    assert gateway.db_path.name == "access_analyst.duckdb"
    assert gateway.db_path != env.root / "platform.duckdb"
    with pytest.raises(QueryDeniedError):
        gateway.query("select email from silver.dim_member")


def test_a_database_error_after_the_sql_checks_is_a_denied_event(env: Env) -> None:
    gateway = env.gateway("analyst")
    with pytest.raises(QueryDeniedError, match="the database refused"):
        gateway.query("select cast(member_key as integer) from dim_member")
    (event,) = env.audit("query_denied")
    assert "the database refused" in event["deny_reason"]


def test_a_failed_audit_write_fails_closed(env: Env) -> None:
    (env.root / "audit").write_text("this is a file, not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        env.gateway("analyst").query("select member_key from dim_member")


def test_unknown_role_and_missing_database_and_empty_principal(env: Env, tmp_path: Path) -> None:
    common: dict[str, Any] = {
        "allowlist_path": env.allowlist_path,
        "warehouse_dir": env.warehouse_dir,
        "audit_root": env.audit_root,
    }
    with pytest.raises(GatewayError, match="unknown role"):
        Gateway("root", "x", **common)
    with pytest.raises(GatewayError, match="principal"):
        Gateway("analyst", "  ", **common)
    with pytest.raises(GatewayError, match="not found"):
        Gateway("analyst", "x", **{**common, "warehouse_dir": tmp_path / "nowhere"})


def test_build_role_databases_validates_the_allowlist(tmp_path: Path) -> None:
    duckdb.connect(str(tmp_path / "w.duckdb")).close()
    bad = {"roles": {"analyst": {"tables": {"t; drop": {"model": "m", "columns": ["a"]}}}}}
    with pytest.raises(ValueError, match="unsafe identifier"):
        build_role_databases(bad, tmp_path / "w.duckdb", tmp_path / "out")
    with pytest.raises(ValueError, match="invalid role name"):
        role_db_path(tmp_path, "../x")
    (tmp_path / "a.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a gateway allowlist"):
        load_allowlist(tmp_path / "a.json")
    assert ALLOWLIST["roles"]


def test_rebuilding_role_databases_replaces_old_files(env: Env) -> None:
    allow = load_allowlist(env.allowlist_path)
    built = build_role_databases(allow, env.root / "platform.duckdb", env.warehouse_dir)
    assert built == {"analyst": 3, "product_a": 1, "privacy_officer": 1}


def test_cli_query_and_build_roles(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    common = [
        "--allowlist", str(env.allowlist_path),
        "--warehouse-dir", str(env.warehouse_dir),
        "--audit-root", str(env.audit_root),
    ]  # fmt: skip
    assert (
        main(
            [
                "query",
                "--role",
                "analyst",
                "--principal",
                "p",
                *common,
                "select n_products from dim_member",
            ]
        )
        == 0
    )
    out = capsys.readouterr()
    assert '"n_products": 1' in out.out and "1 row(s)" in out.err
    assert (
        main(
            [
                "query",
                "--role",
                "analyst",
                "--principal",
                "p",
                *common,
                "select * from read_csv('x')",
            ]
        )
        == 1
    )
    assert "query denied: table functions are not allowed" in capsys.readouterr().err
    assert main(["query", "--role", "nobody", "--principal", "p", *common, "select 1"]) == 2
    assert main([
        "build-roles", "--warehouse", str(env.root / "platform.duckdb"),
        "--allowlist", str(env.allowlist_path), "--out-dir", str(env.warehouse_dir),
    ]) == 0  # fmt: skip
    assert "analyst: 3 tables" in capsys.readouterr().out
