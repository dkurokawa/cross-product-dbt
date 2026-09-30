import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from cross_product_platform.ingest import IngestResult
from cross_product_platform.landing import Landing

from .conftest import TEST_SALT


def test_every_source_lands_and_incoming_is_emptied(lake: IngestResult) -> None:
    report = lake.report
    assert set(report.files_landed) == {"A", "B", "C", "D", "E", "KPI"}
    assert not (lake.root / "_incoming").exists()


def test_bad_files_are_quarantined_with_a_reason_and_never_landed(lake: IngestResult) -> None:
    root = lake.root
    reasons = sorted((root / "quarantine").rglob("*.reason.json"))
    assert {p.parent.parent.name for p in reasons} == {"A", "B", "C", "D", "E"}
    by_source = {json.loads(p.read_text(encoding="utf-8"))["source"]: p for p in reasons}
    e = json.loads(by_source["E"].read_text(encoding="utf-8"))
    assert e["violations"][0]["check"] == "unknown_column_name"
    assert e["violations"][0]["column_name"] == ["備考2"]
    # the offending file sits next to its reason, and is not in the landed tree
    assert Path(str(by_source["E"]).removesuffix(".reason.json")).exists()
    assert not list((root / "E").rglob("*_v2.csv"))
    assert not list((root / "A").rglob("part-bad.parquet"))


def test_quarantine_reasons_hold_no_cell_values(lake: IngestResult) -> None:
    for path in (lake.root / "quarantine").rglob("*.reason.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        for v in body["violations"]:
            assert set(v) <= {"column", "check", "rows", "column_name"}


def test_quarantine_log_matches_report(lake: IngestResult) -> None:
    log = duckdb.sql(
        f"select source, dataset from read_parquet('{lake.root}/_meta/quarantine_log/*.parquet')"
    ).fetchall()
    assert len(log) == len(lake.report.quarantined) == 5


def test_member_keys_are_attached_and_link_across_products(lake: IngestResult) -> None:
    r = lake.root
    con = duckdb.connect()
    a = con.sql(
        f"select member_key, key_method, household_key from "
        f"read_parquet('{r}/A/member_registered/dt=*/*.parquet', union_by_name=true)"
    ).df()
    assert a["member_key"].str.len().eq(64).all()
    assert set(a["key_method"]) <= {"email", "source_local"}
    b = con.sql(
        f"select distinct member_key from read_parquet('{r}/B/customers/dt=*/*.parquet')"
    ).df()
    c = con.sql(
        f"select distinct member_key from read_parquet('{r}/C/members/dt=*/*.parquet')"
    ).df()
    assert len(set(a["member_key"]) & set(b["member_key"])) > 0
    assert len(set(a["member_key"]) & set(c["member_key"])) > 0
    d = con.sql(f"select * from read_csv('{r}/D/invoices/dt=*/*.csv', header=true)").df()
    assert set(d["key_method"]) == {"phone_kana"}
    assert (d["member_key"] == d["link_key"]).all()


def test_d_is_landed_as_utf8_with_original_japanese_headers(lake: IngestResult) -> None:
    sample = next((lake.root / "D" / "invoices").rglob("invoices_2*.csv"))
    text = sample.read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("請求番号,請求日,氏名")


def test_e_is_landed_with_canonical_headers_and_account_key(lake: IngestResult) -> None:
    sample = next((lake.root / "E" / "accounts").rglob("accounts_2*.csv"))
    header = sample.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header[:2] == ["company_name", "contact_name"] and "account_key" in header


def test_households_link_guardians_and_children_across_products(lake: IngestResult) -> None:
    r = lake.root
    rows = duckdb.sql(
        f"select household_key, count(distinct member_key) n "
        f"from read_parquet('{r}/A/member_registered/dt=*/*.parquet', union_by_name=true) "
        f"group by 1 having count(distinct member_key) > 1"
    ).fetchall()
    assert rows, "expected at least one family household in A"


def test_unmatched_counts_are_recorded(lake: IngestResult) -> None:
    stats = pd.read_parquet(lake.root / "_meta" / "identity_stats")
    assert {"A", "B", "C", "D"} <= set(stats["source"])
    a_local = stats[(stats["source"] == "A") & (stats["key_method"] == "source_local")]
    assert int(a_local["n_members"].sum()) > 0  # children without any identifier


def test_the_salt_is_never_written_to_the_lake(lake: IngestResult) -> None:
    needle = TEST_SALT.encode()
    for path in lake.root.rglob("*"):
        if path.is_file():
            assert needle not in path.read_bytes(), path


def test_ingested_at_and_event_schemas_are_kept_for_a(lake: IngestResult) -> None:
    assert (lake.root / "A" / "_schemas" / "member_registered" / "v1.json").exists()
    df = duckdb.sql(
        f"select count(*) filter (where ingested_at is null) "
        f"from read_parquet('{lake.root}/A/app_opened/dt=*/part-*.parquet', union_by_name=true)"
    ).fetchone()
    assert df == (0,)


def test_unreadable_file_is_quarantined(tmp_path: Path) -> None:
    incoming = tmp_path / "_incoming"
    broken = incoming / "B" / "customers" / "dt=2026-02-01"
    broken.mkdir(parents=True)
    (broken / "snapshot.parquet").write_bytes(b"this is not parquet")
    report = Landing(incoming, tmp_path, TEST_SALT.encode()).run()
    (q,) = report.quarantined
    assert (q.source, q.stage, q.partition_date) == ("B", "read", "2026-02-01")
    reason = json.loads(next((tmp_path / "quarantine").rglob("*.reason.json")).read_text("utf-8"))
    assert reason["violations"][0]["check"] in {"ArrowInvalid", "OSError", "ArrowException"}


def test_undecodable_d_file_is_quarantined(tmp_path: Path) -> None:
    incoming = tmp_path / "_incoming"
    target = incoming / "D" / "invoices" / "dt=2026-02-01"
    target.mkdir(parents=True)
    (target / "invoices.csv").write_bytes(b"\xff\xfe\x00\xd8\n\x81")
    report = Landing(incoming, tmp_path, TEST_SALT.encode()).run()
    assert [q.stage for q in report.quarantined] == ["read"]


@pytest.mark.parametrize("name", ["identity_stats", "quarantine_log", "landing_log"])
def test_meta_tables_exist_even_when_empty(tmp_path: Path, name: str) -> None:
    Landing(tmp_path / "_incoming", tmp_path, TEST_SALT.encode()).run()
    assert len(list((tmp_path / "_meta" / name).glob("run-*.parquet"))) == 1


def test_landing_log_counts_files_per_partition(lake: IngestResult) -> None:
    log = pd.read_parquet(lake.root / "_meta" / "landing_log")
    assert int(log["files_landed"].sum()) == sum(lake.report.files_landed.values())
    e = log[(log["source"] == "E") & (log["partition_date"] == "2026-04")]
    assert int(e["files_landed"].sum()) == 1  # the _v2 file next to it was quarantined


def test_a_later_run_adds_to_the_landing_history_instead_of_replacing_it(tmp_path: Path) -> None:
    """`platform ingest --no-generate` on an existing lake used to erase the earlier meta tables."""
    from cross_product_platform.generators.run import generate_all
    from cross_product_platform.ingest import run_ingest

    from .conftest import small_config

    cfg = small_config(n_persons=80, months=2)
    run_ingest(cfg, tmp_path)
    first = {str(p) for p in (tmp_path / "_meta").rglob("run-*.parquet")}
    assert len(first) == 4  # identity_stats, quarantine_log, landing_log, period
    generate_all(cfg, tmp_path / "_incoming")
    run_ingest(cfg, tmp_path, generate=False)
    for name in ("identity_stats", "quarantine_log", "landing_log", "period"):
        files = sorted((tmp_path / "_meta" / name).glob("run-*.parquet"))
        assert len(files) == 2, name
        runs = {str(pd.read_parquet(f)["run_id"].iloc[0]) for f in files}
        assert len(runs) == 2, name
    log = pd.read_parquet(tmp_path / "_meta" / "quarantine_log")
    assert log["run_id"].nunique() == 2 and len(log) == 10  # both runs' 5 quarantined files
