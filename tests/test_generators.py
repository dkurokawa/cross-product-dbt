from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cross_product_platform.config import GeneratorConfig, add_months
from cross_product_platform.generators import world as w
from cross_product_platform.generators.common import kpi_frame, random_uuids
from cross_product_platform.generators.run import generate_all

from .conftest import small_config


def test_config_period_and_months() -> None:
    cfg = GeneratorConfig()
    assert cfg.end == date(2026, 9, 30)
    assert add_months(date(2025, 12, 1), 2) == date(2026, 2, 1)
    assert cfg.fy2026_start == date(2026, 4, 1)


def test_world_is_deterministic_and_has_families() -> None:
    a = w.build_world(small_config())
    b = w.build_world(small_config())
    pd.testing.assert_frame_equal(a.persons, b.persons)
    pd.testing.assert_frame_equal(a.memberships, b.memberships)
    kids = a.persons[a.persons["is_child"]]
    assert len(kids) > 0
    assert (kids["guardian_person_id"] >= 0).all()
    assert not a.persons.loc[~a.persons["is_child"], "email"].isna().any()


def test_overlap_ratio_controls_multi_product_membership() -> None:
    def share_multi(overlap: float) -> float:
        world = w.build_world(small_config(overlap=overlap, n_persons=1500))
        adults = world.persons[~world.persons["is_child"]]["person_id"]
        per_person = world.memberships[world.memberships["person_id"].isin(adults)]
        counts = per_person.groupby("person_id")["product"].nunique()
        return float((counts > 1).mean())

    assert share_multi(0.0) == 0.0
    assert share_multi(0.8) > 0.4


def test_children_join_after_their_guardian_in_the_same_product() -> None:
    world = w.build_world(small_config(n_persons=800))
    ms = world.memberships
    first = ms.set_index(["product", "person_id"])["joined_on"].to_dict()
    for row in ms.itertuples():
        person = world.persons.loc[row.person_id]
        if person["is_child"]:
            guardian_join = first.get((row.product, int(str(person["guardian_person_id"]))))
            assert guardian_join is not None
            assert row.joined_on > guardian_join


def test_kana_and_width_helpers() -> None:
    v = w.kana_variants("サトウショウタ", 3)
    assert v["hiragana"] == "さとうしょうた"
    assert v["spaced"] == "サトウ ショウタ"
    assert v["halfwidth"] == "ｻﾄｳ ｼｮｳﾀ"
    assert w.to_halfwidth_kana("ガ") == "ｶﾞ"
    assert w.to_fullwidth_digits("090-1") == "０９０－１"


def test_sample_days_thins_weekends() -> None:
    rng = np.random.default_rng(1)
    lo = np.zeros(200, dtype=int)
    hi = np.full(200, 6)
    _, off = w.sample_days(rng, lo, hi, np.full(200, 50), date(2026, 2, 2), weekend_weight=0.2)
    weekday = (date(2026, 2, 2).weekday() + off) % 7
    assert (weekday >= 5).sum() < (weekday < 5).sum() * 0.3


def test_random_uuids_are_deterministic_v4() -> None:
    a = random_uuids(np.random.default_rng(3), 5)
    b = random_uuids(np.random.default_rng(3), 5)
    assert a == b and all(u.version == 4 for u in a) and len(set(a)) == 5


def test_kpi_frame_shape() -> None:
    frame = kpi_frame("A", "x", "d", [date(2026, 1, 1)], [3])
    assert list(frame.columns) == ["product", "reported_as", "period_month", "value", "definition"]


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("incoming")
    generate_all(small_config(inject_unmapped_plan=True), root)
    return root


def test_every_source_writes_files(generated: Path) -> None:
    for source in "ABCDE":
        assert any((generated / source).rglob("*.*")), source
    assert (generated / "A" / "_schemas").is_dir()


def test_one_day_gap_removes_that_days_a_events(generated: Path) -> None:
    import duckdb

    glob = str(generated / "A" / "app_opened" / "dt=*" / "part-*.parquet")
    days = {
        str(r[0])
        for r in duckdb.sql(
            f"select distinct cast(occurred_at at time zone 'UTC' as date) "
            f"from read_parquet('{glob}', union_by_name=true)"
        ).fetchall()
    }
    assert "2026-03-10" not in days
    assert "2026-03-09" in days and "2026-03-11" in days


def test_reported_kpis_cover_every_product_and_metric(generated: Path) -> None:
    frames = [pd.read_parquet(p) for p in (generated / "reported_kpis").glob("*/kpis.parquet")]
    kpis = pd.concat(frames)
    assert set(kpis["product"]) == set("ABCDE")
    assert kpis.groupby("reported_as").size().min() == 4  # 4 months each
    names = set(kpis["reported_as"])
    # one same-name-different-definition KPI per product for each of the four metrics
    assert {"active_users", "active_customers", "active_members", "billed_customers",
            "seats_under_contract"} <= names  # fmt: skip
    assert {"iap_revenue", "booking_revenue", "monthly_revenue", "net_sales", "mrr"} <= names
    assert {"lost_customers", "churned_members", "cancelled_customers"} <= names
    assert {"workouts_per_user", "bookings_per_customer", "avg_visits_per_member"} <= names
    assert (kpis["definition"].str.len() > 20).all()


def test_c_has_two_contract_generations_and_repeated_updates(generated: Path) -> None:
    import duckdb

    c = generated / "C"
    fy25 = duckdb.sql(f"select count(*) from read_parquet('{c}/contracts_fy2025/dt=*/*.parquet')")
    fy26 = duckdb.sql(f"select count(*) from read_parquet('{c}/contracts_fy2026/dt=*/*.parquet')")
    assert fy25.fetchone()[0] > 0 and fy26.fetchone()[0] > 0  # type: ignore[index]
    dup = duckdb.sql(
        f"select max(n) from (select contract_id, count(*) n "
        f"from read_parquet('{c}/contracts_fy2026/dt=*/*.parquet') group by 1)"
    ).fetchone()
    assert dup is not None and dup[0] >= 2
    unmapped = duckdb.sql(
        f"select count(*) from read_parquet('{c}/contracts_fy2026/dt=*/*.parquet') "
        f"where plan_id = 'P999'"
    ).fetchone()
    assert unmapped == (1,)


def test_d_files_are_shift_jis_with_japanese_headers(generated: Path) -> None:
    sample = next((generated / "D" / "invoices").rglob("invoices_2*.csv"))
    raw = sample.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    text = raw.decode("cp932")
    assert text.splitlines()[0].startswith("請求番号,請求日,氏名")
    assert "/" in text.splitlines()[1]


def test_e_header_spellings_drift_between_months(generated: Path) -> None:
    headers = {
        f.read_text(encoding="utf-8-sig").splitlines()[0].split(",")[0]
        for f in (generated / "E" / "accounts").rglob("accounts_2*.csv")
        if "_v2" not in f.name
    }
    assert headers == {"会社名", "企業名"}


def test_b_uses_tenant_scoped_numbers_and_naive_timestamps(generated: Path) -> None:
    snap = sorted((generated / "B" / "customers").rglob("snapshot.parquet"))[-1]
    df = pd.read_parquet(snap)
    assert df["created_at"].dt.tz is None
    assert df.groupby("tenant_id")["member_no"].min().eq(1).all()
    assert df.duplicated(["tenant_id", "member_no"]).sum() == 0
    assert df["is_deleted"].any()
