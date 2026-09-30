from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cross_product_platform.cli import main
from cross_product_platform.ingest import IngestResult
from cross_product_platform.scan import (
    check_model_declarations,
    detect,
    load_source_declarations,
    scan_lake,
)

REPO = Path(__file__).resolve().parent.parent
SOURCES = REPO / "dbt" / "models" / "bronze" / "_sources.yml"


def test_detect_by_column_name() -> None:
    assert detect("guardian_email", []).level == "direct"
    assert detect("Mobile_Number", []).level == "direct"
    assert detect("生年月日", []).level == "direct"
    assert detect("氏名カナ", []).level == "direct"
    assert detect("contact_name", []).level == "direct"
    assert detect("home_address", []).level == "direct"
    assert detect("medical_conditions", []).level == "sensitive"
    assert detect("event_id", ["abc"]).level == "none"


def test_detect_does_not_flag_names_of_things() -> None:
    for column in ("company_name", "plan_name", "file_name", "facility_id", "sku", "status"):
        assert detect(column, ["x"]).level == "none", column


def test_detect_by_values() -> None:
    assert detect("col_a", ["a@example.com", "b@example.org"]).level == "direct"
    assert detect("col_b", ["090-1234-5678", "０９０－１２３４－５６７８"]).level == "direct"
    text = ["Left knee ligament injury in 2025, rehabilitation ongoing."] * 3
    found = detect("notes", text)
    assert found.level == "sensitive" and "free text" in found.reasons[0]
    assert detect("keys", ["a" * 64] * 5).level == "none"
    assert detect("stamp", ["2026-09-30 11:36:08+00:00"] * 5).level == "none"
    assert detect("ids", ["INV0000001"] * 5).level == "none"
    assert detect("empty", [None, "", "  "]).level == "none"


def test_declarations_are_read_from_the_sources_file() -> None:
    declared = load_source_declarations(SOURCES)
    assert declared["A", "member_registered"]["email"] == "direct"
    assert declared["C", "members"]["medical_conditions"] == "sensitive"
    assert declared["D", "invoices"]["氏名カナ"] == "direct"
    assert declared["E", "accounts"]["contact_name"] == "direct"


def test_the_landed_lake_is_fully_declared(lake: IngestResult) -> None:
    problems, warnings = scan_lake(lake.root, load_source_declarations(SOURCES))
    assert problems == []
    assert warnings == []


def _mini_lake(root: Path, extra: dict[str, list[str]] | None = None) -> None:
    target = root / "A" / "member_registered" / "dt=2026-01-01"
    target.mkdir(parents=True)
    data: dict[str, list[str]] = {
        "event_id": ["e1", "e2"],
        "email": ["a@example.com", "b@example.com"],
        "note": ["short", "text"],
    }
    data.update(extra or {})
    pd.DataFrame(data).to_parquet(target / "part-1.parquet", index=False)


def test_scan_fails_on_an_undeclared_pii_column(tmp_path: Path) -> None:
    _mini_lake(tmp_path, {"mobile_number": ["090-1234-5678", "080-1111-2222"]})
    declared = {("A", "member_registered"): {"email": "direct"}}
    problems, _ = scan_lake(tmp_path, declared)
    assert len(problems) == 1
    assert "A.member_registered.mobile_number" in problems[0] and "undeclared" in problems[0]


def test_scan_fails_when_pii_is_declared_none(tmp_path: Path) -> None:
    _mini_lake(tmp_path)
    problems, _ = scan_lake(tmp_path, {("A", "member_registered"): {"email": "none"}})
    assert len(problems) == 1 and "declared none" in problems[0]


def test_scan_fails_on_undeclared_free_text_and_invalid_class(tmp_path: Path) -> None:
    long = "Recovering from a wrist fracture, no push-ups."
    _mini_lake(tmp_path, {"remarks": [long, long]})
    declared = {("A", "member_registered"): {"email": "secret"}}
    problems, _ = scan_lake(tmp_path, declared)
    assert any("not one of" in p for p in problems)
    assert any("remarks" in p and "free text" in p for p in problems)


def test_scan_fails_when_declared_weaker_than_detected(tmp_path: Path) -> None:
    _mini_lake(tmp_path)
    problems, warnings = scan_lake(tmp_path, {("A", "member_registered"): {"email": "quasi"}})
    assert len(problems) == 1
    assert "declared quasi but detected direct" in problems[0]
    assert warnings == []


def test_scan_sees_pii_that_is_only_in_one_of_many_files(tmp_path: Path) -> None:
    """Every landed file is scanned; a leak in file 2 of 10 must not slip through a sample."""
    directory = tmp_path / "A" / "member_registered"
    for i in range(10):
        target = directory / f"dt=2026-01-{i + 1:02d}"
        target.mkdir(parents=True)
        data: dict[str, list[str]] = {"event_id": ["e"]}
        if i == 1:
            data["mobile_number"] = ["090-1234-5678"]
        pd.DataFrame(data).to_parquet(target / "part-1.parquet", index=False)
    problems, _ = scan_lake(tmp_path, {("A", "member_registered"): {}})
    assert len(problems) == 1 and "mobile_number" in problems[0]

    csv_dir = tmp_path / "E" / "accounts"
    for i in range(10):
        target = csv_dir / f"month=2026-{i + 1:02d}"
        target.mkdir(parents=True)
        note = "Recovering from a wrist fracture and no push-ups." if i == 1 else "x"
        (target / "a.csv").write_text(f"company_name,remarks\nAcme,{note}\n", encoding="utf-8")
    problems, _ = scan_lake(tmp_path, {("E", "accounts"): {}})
    assert any("remarks" in p and "free text" in p for p in problems)


def test_scan_reads_csv_headers_and_values(tmp_path: Path) -> None:
    target = tmp_path / "E" / "accounts" / "month=2026-01"
    target.mkdir(parents=True)
    (target / "a.csv").write_text("company_name,tel\nAcme,03-1234-5678\n", encoding="utf-8")
    problems, _ = scan_lake(tmp_path, {})
    assert len(problems) == 1 and "E.accounts.tel" in problems[0]


def _node(name: str, schema: str, columns: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"resource_type": "model", "name": name, "schema": schema, "columns": columns}


def _col(pii: str | None, mask: str | None = None) -> dict[str, Any]:
    if pii is None:
        return {}
    meta: dict[str, Any] = {"pii": pii}
    if mask:
        meta["mask"] = mask
    return {"config": {"meta": meta}}


def test_model_declarations_must_be_complete_and_valid() -> None:
    manifest = {
        "nodes": {
            "model.p.a": _node("dim_a", "silver", {"id": _col("none"), "tel": _col(None)}),
            "model.p.b": _node("dim_b", "silver", {"mail": _col("direct")}),
            "model.p.c": _node("dim_c", "gold", {"x": _col("pii?")}),
            "model.p.d": _node("brz_d", "bronze", {"y": _col(None)}),
            "model.p.e": _node("acc_e", "access", {"mail": _col("direct")}),
            "seed.p.f": {"resource_type": "seed", "name": "s", "schema": "silver", "columns": {}},
        }
    }
    catalog: dict[str, Any] = {
        "nodes": {"model.p.a": {"columns": {"id": {}, "TEL": {}, "extra": {}}}}
    }
    problems = check_model_declarations(manifest, catalog)
    assert "dim_a.TEL: no meta.pii declaration" in problems
    assert "dim_a.extra: no meta.pii declaration" in problems
    assert any(p.startswith("dim_b.mail: direct column needs meta.mask") for p in problems)
    assert any(p.startswith("dim_c.x: invalid meta.pii") for p in problems)
    assert not any("brz_d" in p or "acc_e" in p for p in problems)  # bronze exempt; access ok
    manifest["nodes"]["model.p.d"]["schema"] = "silver"
    assert any("brz_d.y" in p for p in check_model_declarations(manifest, None))


def test_scan_cli(lake: IngestResult, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["scan", "--root", str(lake.root), "--sources", str(SOURCES)]
    assert main([*args, "--skip-models"]) == 0
    assert "0 problem(s)" in capsys.readouterr().out
    assert main([*args, "--manifest", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "manifest.json"
    bad.write_text(
        '{"nodes": {"model.p.a": {"resource_type": "model", "name": "m", '
        '"schema": "silver", "columns": {"c": {}}}}}',
        encoding="utf-8",
    )
    assert main([*args, "--manifest", str(bad), "--catalog", str(tmp_path / "none.json")]) == 1
    assert "m.c: no meta.pii declaration" in capsys.readouterr().err
    _mini_lake(tmp_path / "lk", {"mobile_number": ["090-1234-5678", "080-1111-2222"]})
    (tmp_path / "s.yml").write_text("sources: []\n", encoding="utf-8")
    bad_args = ["scan", "--root", str(tmp_path / "lk"), "--sources", str(tmp_path / "s.yml")]
    assert main([*bad_args, "--skip-models"]) == 1
    assert "mobile_number" in capsys.readouterr().err


def test_every_column_needs_a_description() -> None:
    from cross_product_platform.scan import check_descriptions

    def col(text: str | None) -> dict[str, Any]:
        return {} if text is None else {"description": text}

    manifest = {
        "nodes": {
            "model.p.a": _node("dim_a", "silver", {"id": col("An id."), "tel": col("  ")}),
            "model.p.b": _node("acc_b", "access", {"x": col(None)}),
            "model.p.c": _node("brz_c", "bronze", {"y": col(None)}),
            "seed.p.d": {
                "resource_type": "seed",
                "name": "s",
                "schema": "silver",
                "columns": {"z": col("ok")},
            },  # fmt: skip
        }
    }
    catalog: dict[str, Any] = {"nodes": {"model.p.a": {"columns": {"id": {}, "EXTRA": {}}}}}
    problems = check_descriptions(manifest, catalog)
    assert sorted(problems) == [
        "acc_b.x: no description", "dim_a.EXTRA: no description", "dim_a.tel: no description",
    ]  # fmt: skip
    assert check_descriptions(manifest, None) == [
        "dim_a.tel: no description",
        "acc_b.x: no description",
    ]
