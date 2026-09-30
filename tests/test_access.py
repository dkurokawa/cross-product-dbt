import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from cross_product_platform.access import (
    ColumnInfo,
    PolicyError,
    check_access_files,
    check_access_manifest,
    generate_access_files,
    load_columns,
    load_policy,
    model_name,
    parse_policy,
    write_access_files,
)
from cross_product_platform.cli import main
from cross_product_platform.metrics.spec import load_spec

REPO = Path(__file__).resolve().parent.parent
POLICY = REPO / "policies" / "access.yml"


@pytest.fixture(scope="module")
def files() -> dict[str, str]:
    policy = load_policy(POLICY)
    return generate_access_files(
        policy, load_columns(REPO, load_spec(REPO / "metrics/metrics.yml"))
    )


def raw() -> dict[str, Any]:
    loaded = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return copy.deepcopy(loaded)


def test_committed_access_layer_and_allowlist_are_up_to_date(files: dict[str, str]) -> None:
    assert check_access_files(files, REPO) == []


def test_analyst_sees_masked_direct_columns_and_no_sensitive_ones(files: dict[str, str]) -> None:
    sql = files["dbt/models/access/acc_analyst__dim_member.sql"]
    assert "mask_email_domain('email') }} as email_domain" in sql
    assert "mask_phone_last4('phone') }} as phone_last4" in sql
    assert "mask_birth_year('birth_date') }} as birth_date_year" in sql
    assert "health_notes" not in sql and "medical_conditions" not in sql
    assert "\n    email\n" not in sql and "\n    phone,\n" not in sql
    assert "where" not in sql  # the analyst sees every product


def test_no_restricted_role_ever_gets_a_sensitive_or_raw_direct_column(
    files: dict[str, str],
) -> None:
    for path, sql in files.items():
        if not path.endswith(".sql") or "privacy_officer" in path:
            continue
        for forbidden in ("health_notes", "medical_conditions"):
            assert forbidden not in sql, path
        for raw_column in (
            "    email,",
            "    email\n",
            "    phone,",
            "    birth_date,",
            "    name,",
        ):
            assert raw_column not in sql, (path, raw_column)


def test_privacy_officer_gets_everything_unmasked(files: dict[str, str]) -> None:
    sql = files["dbt/models/access/acc_privacy_officer__dim_member.sql"]
    for column in ("health_notes", "medical_conditions", "email", "phone", "birth_date"):
        assert f"    {column}" in sql
    assert "mask_" not in sql


def test_product_roles_are_row_scoped(files: dict[str, str]) -> None:
    assert files["dbt/models/access/acc_product_a__dim_member.sql"].endswith("where in_a\n")
    assert files["dbt/models/access/acc_product_b__fct_payment.sql"].endswith(
        "where source = 'B'\n"
    )
    assert files["dbt/models/access/acc_product_c__metric_active_members.sql"].endswith(
        "where product = 'C'\n"
    )
    # a fixed-product table only goes to that product's role
    assert "dbt/models/access/acc_product_b__fct_booking.sql" in files
    assert "dbt/models/access/acc_product_a__fct_booking.sql" not in files
    assert "dbt/models/access/acc_product_a__dim_account.sql" not in files
    assert "dbt/models/access/acc_product_e__dim_account.sql" in files
    # E has no members, so no member flag to filter by
    assert "dbt/models/access/acc_product_e__dim_member.sql" not in files
    # reference data is visible to everybody, unfiltered
    assert files["dbt/models/access/acc_product_a__dim_plan.sql"].count("where") == 0


def test_allowlist_lists_tables_and_columns_per_role(files: dict[str, str]) -> None:
    allow = json.loads(files["policies/gateway_allowlist.json"])
    assert "do not edit" in allow["generated"]
    assert set(allow["roles"]) == {
        "analyst", "product_a", "product_b", "product_c", "product_d", "product_e",
        "privacy_officer",
    }  # fmt: skip
    analyst = allow["roles"]["analyst"]["tables"]["dim_member"]
    assert analyst["model"] == "acc_analyst__dim_member"
    assert "email_domain" in analyst["columns"] and "email" not in analyst["columns"]
    officer = allow["roles"]["privacy_officer"]["tables"]["dim_member"]["columns"]
    assert "health_notes" in officer
    for role, body in allow["roles"].items():
        if role != "privacy_officer":
            for table in body["tables"].values():
                assert not {"health_notes", "medical_conditions"} & set(table["columns"])


def test_access_yml_declares_masked_outputs_as_quasi(files: dict[str, str]) -> None:
    doc = yaml.safe_load(files["dbt/models/access/_access.yml"])
    model = next(m for m in doc["models"] if m["name"] == "acc_analyst__dim_member")
    classes = {c["name"]: c["config"]["meta"]["pii"] for c in model["columns"]}
    assert classes["email_domain"] == "quasi" and classes["member_key"] == "quasi"
    officer = next(m for m in doc["models"] if m["name"] == "acc_privacy_officer__dim_member")
    assert {c["name"]: c["config"]["meta"]["pii"] for c in officer["columns"]}["email"] == "direct"


def test_check_fails_on_edit_missing_and_stale_files(files: dict[str, str], tmp_path: Path) -> None:
    write_access_files(files, tmp_path)
    assert check_access_files(files, tmp_path) == []
    victim = tmp_path / "dbt/models/access/acc_analyst__dim_member.sql"
    victim.write_text(victim.read_text(encoding="utf-8") + "-- edited\n", encoding="utf-8")
    (tmp_path / "policies/gateway_allowlist.json").unlink()
    (tmp_path / "dbt/models/access/acc_ghost__x.sql").write_text("select 1", encoding="utf-8")
    problems = check_access_files(files, tmp_path)
    assert any("out of date or was edited" in p for p in problems)
    assert any("missing" in p for p in problems)
    assert any("stale generated file" in p for p in problems)
    write_access_files(files, tmp_path)
    assert check_access_files(files, tmp_path) == []


def test_manifest_check_flags_sensitive_columns_in_a_restricted_role(files: dict[str, str]) -> None:
    policy = load_policy(POLICY)

    def node(name: str, pii: str) -> dict[str, Any]:
        return {
            "resource_type": "model",
            "schema": "access",
            "name": name,
            "columns": {"c": {"config": {"meta": {"pii": pii}}}},
        }

    manifest = {
        "nodes": {
            "a": node("acc_analyst__dim_member", "sensitive"),
            "b": node("acc_product_a__dim_member", "direct"),
            "c": node("acc_privacy_officer__dim_member", "sensitive"),
            "d": node("acc_analyst__fct_payment", "quasi"),
            "e": {"resource_type": "model", "schema": "silver", "name": "x", "columns": {}},
            "f": node("unrelated", "sensitive"),
        }
    }
    problems = check_access_manifest(policy, manifest)
    assert len(problems) == 2
    assert "acc_analyst__dim_member.c is sensitive" in problems[0]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.update(version=3), "version"),
        (lambda r: r.update(roles={}), "no roles"),
        (lambda r: r["roles"]["analyst"].update(products=[]), "products"),
        (lambda r: r["roles"]["analyst"].update(sensitive="maybe"), "sensitive"),
        (lambda r: r["roles"]["analyst"].update(direct="maybe"), "direct"),
        (lambda r: r["tables"][0]["row_scope"].update(kind="teleport"), "unknown row_scope"),
        (lambda r: r["tables"][0]["row_scope"].update(columns={}), "missing its parameters"),
    ],
)
def test_policy_validation(mutate: Any, message: str) -> None:
    doc = raw()
    mutate(doc)
    with pytest.raises(PolicyError, match=message):
        parse_policy(doc)


def test_unknown_table_and_undeclared_column_are_rejected() -> None:
    columns = load_columns(REPO, load_spec(REPO / "metrics/metrics.yml"))
    doc = raw()
    doc["tables"].append({"name": "ghost", "layer": "silver", "row_scope": {"kind": "none"}})
    with pytest.raises(PolicyError, match="no declared columns"):
        generate_access_files(parse_policy(doc), columns)
    assert model_name(parse_policy(raw()).roles[0], "t") == "acc_analyst__t"


def test_a_direct_column_without_a_mask_cannot_be_generated(tmp_path: Path) -> None:
    for rel in ("dbt/models/silver", "dbt/models/gold", "dbt/seeds"):
        shutil.copytree(REPO / rel, tmp_path / rel)
    path = tmp_path / "dbt/models/silver/_models.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for model in doc["models"]:
        for column in model.get("columns", []):
            if column["name"] == "email":
                column["config"]["meta"].pop("mask", None)
    path.write_text(yaml.dump(doc), encoding="utf-8")
    with pytest.raises(PolicyError, match="direct needs a valid mask"):
        load_columns(tmp_path, load_spec(REPO / "metrics/metrics.yml"))
    for model in doc["models"]:
        for column in model.get("columns", []):
            if column["name"] == "email":
                column["config"]["meta"].pop("pii")
    path.write_text(yaml.dump(doc), encoding="utf-8")
    with pytest.raises(PolicyError, match="missing or invalid meta.pii"):
        load_columns(tmp_path, load_spec(REPO / "metrics/metrics.yml"))


def test_column_info_defaults() -> None:
    assert ColumnInfo("x", "none").mask is None


def test_cli_generate_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for rel in ("dbt/models/silver", "dbt/models/gold", "dbt/seeds", "policies", "metrics"):
        shutil.copytree(REPO / rel, tmp_path / rel, ignore=shutil.ignore_patterns("acc_*"))
    shutil.rmtree(tmp_path / "dbt/models/access", ignore_errors=True)
    args = ["--repo-root", str(tmp_path), "--policy", str(tmp_path / "policies/access.yml"),
            "--spec", str(tmp_path / "metrics/metrics.yml")]  # fmt: skip
    assert main(["access", "check", *args]) == 1  # nothing generated yet
    assert main(["access", "generate", *args]) == 0
    assert main(["access", "check", *args]) == 0
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"nodes": {"a": {"resource_type": "model", "schema": "access", '
        '"name": "acc_analyst__dim_member", "columns": {"h": {"config": '
        '{"meta": {"pii": "sensitive"}}}}}}}',
        encoding="utf-8",
    )
    assert main(["access", "check", *args, "--manifest", str(manifest)]) == 1
    assert "is sensitive in a restricted role" in capsys.readouterr().err
    assert main(["access", "check", "--policy", str(tmp_path / "nope.yml"), *args[:2]]) == 2
