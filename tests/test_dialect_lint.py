from pathlib import Path

from cross_product_platform.cli import main
from cross_product_platform.dialect_lint import scan_directory, scan_text


def test_flags_duckdb_and_bigquery_specific_constructs() -> None:
    sql = "select strptime(a, 'x'), safe_cast(b as int), c::int, countif(d) from t"
    reasons = {v.reason for v in scan_text(Path("m.sql"), sql)}
    assert len(reasons) == 4


def test_macro_calls_and_comments_are_not_flagged() -> None:
    sql = (
        "-- strptime is only mentioned in a comment\n"
        "{# safe_cast in a jinja comment #}\n"
        "select {{ dbt.safe_cast('a', 'int') }}, 'strptime(' as literal from t\n"
    )
    assert scan_text(Path("m.sql"), sql) == []


def test_at_time_zone_is_flagged() -> None:
    (v,) = scan_text(Path("m.sql"), "select ts AT TIME ZONE 'UTC' from t")
    assert v.line == 1
    assert "to_utc_from_jst" in v.reason
    assert str(v).startswith("m.sql:1:")


def test_scan_directory_and_cli_exit_codes(tmp_path: Path) -> None:
    (tmp_path / "ok.sql").write_text("select 1", encoding="utf-8")
    assert scan_directory(tmp_path) == []
    assert main(["lint-dialect", str(tmp_path)]) == 0
    (tmp_path / "bad.sql").write_text("select read_parquet('x')", encoding="utf-8")
    assert main(["lint-dialect", str(tmp_path)]) == 1
