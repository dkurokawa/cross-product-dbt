import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from cross_product_platform.cli import main
from cross_product_platform.metrics.check import (
    check_generated,
    check_manifest,
    check_reported,
    load_json,
    write_files,
)
from cross_product_platform.metrics.codegen import describe, generate_files, generated_columns
from cross_product_platform.metrics.spec import MetricsSpec, SpecError, load_spec, parse_spec

REPO = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO / "metrics" / "metrics.yml"


@pytest.fixture(scope="module")
def spec() -> MetricsSpec:
    return load_spec(SPEC_PATH)


def raw() -> dict[str, Any]:
    loaded = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return copy.deepcopy(loaded)


def test_spec_has_the_four_metrics_and_a_variant_for_every_product(spec: MetricsSpec) -> None:
    assert [m.name for m in spec.metrics] == [
        "active_members",
        "net_revenue",
        "churned_members",
        "sessions_per_active_member",
    ]
    products = {p for p, _ in spec.reported_names()}
    assert products == set("ABCDE")
    assert spec.scopes_of(spec.by_name("churned_members"))["D"] == "population"
    assert describe(spec)[0] == {"metric": "active_members", "variants": 5}


def test_generation_is_deterministic_and_complete(spec: MetricsSpec) -> None:
    files = generate_files(spec)
    assert files == generate_files(spec)
    assert len(files) == 4 * 4 + 1
    for path, text in files.items():
        first = text.splitlines()[0]
        assert "GENERATED" in first and "do not edit" in first, path
    assert "recon_net_revenue" in generated_columns(spec)


def test_committed_files_match_the_generator(spec: MetricsSpec) -> None:
    assert check_generated(spec, REPO) == []


def test_check_1_fails_when_a_generated_file_is_edited(spec: MetricsSpec, tmp_path: Path) -> None:
    write_files(generate_files(spec), tmp_path)
    assert check_generated(spec, tmp_path) == []
    victim = tmp_path / "dbt/models/gold/metrics/metric_active_members.sql"
    victim.write_text(victim.read_text(encoding="utf-8") + "\n-- hand edit\n", encoding="utf-8")
    (tmp_path / "dbt/models/gold/recon/recon_net_revenue.yml").unlink()
    (tmp_path / "dbt/models/gold/metrics/metric_extra.sql").write_text("select 1", encoding="utf-8")
    problems = check_generated(spec, tmp_path)
    assert any("out of date or was edited" in p for p in problems)
    assert any("missing" in p for p in problems)
    assert any("stale generated file" in p for p in problems)
    write_files(generate_files(spec), tmp_path)  # regenerate repairs it and removes the stale file
    assert check_generated(spec, tmp_path) == []


def _manifest(name: str, path: str, columns: list[str]) -> dict[str, Any]:
    return {
        "nodes": {
            f"model.platform.{name}": {
                "resource_type": "model",
                "name": name,
                "original_file_path": path,
                "columns": {c: {} for c in columns},
            }
        }
    }


def test_check_2_fails_on_a_hand_written_column_named_like_a_metric(spec: MetricsSpec) -> None:
    bad = _manifest("dim_thing", "models/silver/dim_thing.sql", ["id", "Active_Members"])
    assert check_manifest(spec, bad)[0].startswith("dim_thing creates column 'active_members'")
    sneaky = _manifest("dim_x", "models/silver/dim_x.sql", ["id"])
    catalog: dict[str, Any] = {"nodes": {"model.platform.dim_x": {"columns": {"MRR": {}}}}}
    assert "'mrr'" in check_manifest(spec, sneaky, catalog)[0]


def test_check_2_allows_generated_models_and_clean_models(spec: MetricsSpec) -> None:
    generated = _manifest(
        "metric_active_members", "models/gold/metrics/metric_active_members.sql", ["active_members"]
    )
    assert check_manifest(spec, generated) == []
    clean = _manifest("dim_x", "models/silver/dim_x.sql", ["id", "n_members"])
    assert check_manifest(spec, clean) == []
    assert check_manifest(spec, {"nodes": {"seed.x": {"resource_type": "seed"}}}) == []


def _kpis(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    for product, reported_as in rows:
        target = tmp_path / "reported_kpis" / product
        target.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"product": [product], "reported_as": [reported_as]}).to_parquet(
            target / "kpis.parquet"
        )
    return tmp_path


def test_check_3_fails_on_a_kpi_that_is_not_a_known_variant(
    spec: MetricsSpec, tmp_path: Path
) -> None:
    lake = _kpis(tmp_path, [("A", "active_users"), ("A", "daily_glow")])
    (problem,) = check_reported(spec, lake)
    assert "daily_glow" in problem
    assert check_reported(spec, _kpis(tmp_path / "ok", [("A", "active_users")])) == []
    assert "no reported_kpis" in check_reported(spec, tmp_path / "empty")[0]


def test_check_3_accepts_what_the_generators_really_emit(
    spec: MetricsSpec, lake_root: Path
) -> None:
    assert check_reported(spec, lake_root) == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.update(version=2), "version"),
        (lambda r: r["metrics"][0].update(kind="nope"), "kind must be"),
        (lambda r: r["metrics"][0].update(name=""), "no name"),
        (lambda r: r["metrics"].append(copy.deepcopy(r["metrics"][0])), "duplicate metric"),
        (lambda r: r["metrics"][0]["scopes"].update(A="bogus"), "scope"),
        (
            lambda r: r["metrics"][0]["known_variants"][0].update(expected_direction="up"),
            "expected_direction",
        ),
        (lambda r: r["metrics"][0]["known_variants"][0].pop("threshold_pct"), "threshold_pct"),
        (lambda r: r["metrics"][0]["known_variants"][4].pop("note"), "needs a note"),
        (lambda r: r["metrics"][0]["known_variants"][0].pop("reported_as"), "missing"),
        (
            lambda r: r["metrics"][1]["known_variants"].append(
                copy.deepcopy(r["metrics"][0]["known_variants"][0])
            ),
            "two metrics",
        ),
        (lambda r: r["metrics"][2]["definition"].update(active_metric="net_revenue"), "window"),
    ],
)
def test_spec_validation_rejects_bad_files(mutate: Any, message: str) -> None:
    doc = raw()
    mutate(doc)
    with pytest.raises(SpecError, match=message):
        parse_spec(doc)


def test_unknown_metric_and_missing_scopes() -> None:
    doc = raw()
    doc["metrics"][2]["definition"]["active_metric"] = "ghost"
    with pytest.raises(SpecError, match="unknown metric"):
        parse_spec(doc)


def test_population_scope_is_rejected_for_sums(spec: MetricsSpec) -> None:
    doc = raw()
    doc["metrics"][1]["scopes"]["D"] = "population"
    with pytest.raises(SpecError, match="population"):
        generate_files(parse_spec(doc))


def test_cli_generate_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "repo"
    (root / "metrics").mkdir(parents=True)
    shutil.copy(SPEC_PATH, root / "metrics" / "metrics.yml")
    spec_arg = ["--spec", str(root / "metrics" / "metrics.yml"), "--repo-root", str(root)]
    assert main(["metrics", "generate", *spec_arg]) == 0
    assert main(["metrics", "check", *spec_arg, "--only-generated"]) == 0
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(_manifest("dim_x", "models/silver/dim_x.sql", ["id"])))
    args = [*spec_arg, "--manifest", str(manifest), "--catalog", str(root / "none.json")]
    assert main(["metrics", "check", *args]) == 0
    manifest.write_text(json.dumps(_manifest("dim_x", "models/silver/dim_x.sql", ["mrr"])))
    assert main(["metrics", "check", *args]) == 1
    assert "creates column 'mrr'" in capsys.readouterr().err
    assert main(["metrics", "check", *spec_arg, "--manifest", str(root / "missing.json")]) == 2
    assert main(["metrics", "generate", "--spec", str(root / "nope.yml")]) == 2
    assert load_json(manifest)["nodes"]
    manifest.write_text("[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        load_json(manifest)


def test_generated_metrics_use_the_month_spine_and_emit_every_scope_with_zero(
    spec: MetricsSpec,
) -> None:
    """A month or scope with no activity must still have a row (0), from dim_month."""
    files = generate_files(spec)
    for metric in spec.metrics:
        sql = files[f"dbt/models/gold/metrics/metric_{metric.name}.sql"]
        assert "ref('dim_month')" in sql, metric.name
        assert "cross join products as p" in sql and "coalesce(" in sql, metric.name
        assert "select distinct\n        {{ month_start" not in sql  # not months seen in the facts
        yml = files[f"dbt/models/gold/metrics/metric_{metric.name}.yml"]
        assert "metric_covers_period_and_scopes" in yml
    active = files["dbt/models/gold/metrics/metric_active_members.yml"]
    assert "products: [ALL, A, B, C, D]" in active
    revenue = files["dbt/models/gold/metrics/metric_net_revenue.yml"]
    assert "products: [ALL, A, D]" in revenue
