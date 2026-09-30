"""Product A: consumer app + studio-search media, written as eventlake events.

Deliberate inconsistencies of A (each is normalized in the dbt bronze/silver layer):
timestamps are UTC, ids are UUIDs, in-app purchases are tax-inclusive, and there are
family accounts (a guardian registers a child, who may have no e-mail of their own).
A second writer re-delivers a small share of events, which is what the event_id
de-duplication in bronze is for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from eventlake import Writer

from ..config import GeneratorConfig
from .common import (
    instants,
    kpi_frame,
    month_end,
    month_starts,
    random_uuids,
    rolling_distinct,
)
from .events_a import AppOpened, MemberRegistered, PurchaseMade, WorkoutCompleted
from .world import World, kana_variants, records, sample_days, span_bounds

SKUS: dict[str, int] = {
    "premium_month": 2980,
    "day_pass": 1100,
    "pt_pack_5": 24200,
    "protein_bar_box": 3300,
}
WORKOUT_TYPES = ("strength", "cardio", "yoga", "pilates", "hiit")
TAX_RATE = 0.10


def _format_phone(digits: str) -> str:
    return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"


def generate_a(world: World, rng: np.random.Generator, out_dir: Path) -> pd.DataFrame:
    """Write product A's events under ``out_dir`` and return its self-reported KPIs."""
    cfg = world.cfg
    members = world.members("A")
    n = len(members)
    member_ids = random_uuids(rng, n)
    id_by_person = {int(p): member_ids[i] for i, p in enumerate(members["person_id"])}
    email_by_person = dict(zip(world.persons["person_id"], world.persons["email"], strict=True))

    lo, hi = span_bounds(cfg, members["joined_on"], members["left_on"])
    months_active = (hi - lo + 1) / 30.4

    # --- registrations -------------------------------------------------------------
    reg_off = (pd.to_datetime(members["joined_on"]) - pd.Timestamp(cfg.start)).dt.days.to_numpy()
    reg_ts = instants(rng, cfg.start, reg_off)
    reg_events: list[tuple[np.datetime64, MemberRegistered]] = []
    reg_ids = random_uuids(rng, n)
    for i, row in enumerate(records(members)):
        guardian = int(row["guardian_person_id"])
        kana = kana_variants(str(row["kana"]), int(row["kana_family_len"]))["hiragana"]
        email = None if row["email"] is None or pd.isna(row["email"]) else str(row["email"]).lower()
        reg_events.append(
            (
                reg_ts[i],
                MemberRegistered(
                    event_id=reg_ids[i],
                    occurred_at=_utc(reg_ts[i]),
                    member_id=member_ids[i],
                    email=email,
                    name=str(row["name"]),
                    name_kana=kana,
                    phone=None
                    if row["phone"] is None or pd.isna(row["phone"])
                    else _format_phone(str(row["phone"])),
                    birth_date=row["birth_date"],
                    guardian_member_id=id_by_person.get(guardian) if row["is_child"] else None,
                    guardian_email=(
                        str(email_by_person[guardian]).lower() if row["is_child"] else None
                    ),
                    health_notes=None if pd.isna(row["health_note"]) else str(row["health_note"]),
                ),
            )
        )

    # --- activity ------------------------------------------------------------------
    opens_idx, opens_off = sample_days(
        rng, lo, hi, rng.poisson(rng.gamma(2.0, 2.0, n) * months_active), cfg.start
    )
    work_idx, work_off = sample_days(
        rng, lo, hi, rng.poisson(rng.gamma(1.5, 1.6, n) * months_active), cfg.start
    )
    buy_idx, buy_off = sample_days(
        rng, lo, hi, rng.poisson(0.45 * months_active), cfg.start, weekend_weight=1.0
    )
    opens_ts = instants(rng, cfg.start, opens_off)
    work_ts = instants(rng, cfg.start, work_off)
    buy_ts = instants(rng, cfg.start, buy_off)
    sku_names = list(SKUS)
    sku_choice = rng.choice(len(sku_names), size=len(buy_idx), p=[0.5, 0.25, 0.1, 0.15])
    w_type = rng.integers(0, len(WORKOUT_TYPES), size=len(work_idx))
    w_dur = rng.integers(20, 90, size=len(work_idx))
    opens_ids = random_uuids(rng, len(opens_idx))
    work_ids = random_uuids(rng, len(work_idx))
    buy_ids = random_uuids(rng, len(buy_idx))

    events: list[tuple[np.datetime64, object]] = list(reg_events)
    events += [
        (opens_ts[k], AppOpened(event_id=opens_ids[k], occurred_at=_utc(opens_ts[k]),
                                member_id=member_ids[int(opens_idx[k])]))
        for k in range(len(opens_idx))
    ]  # fmt: skip
    events += [
        (
            work_ts[k],
            WorkoutCompleted(
                event_id=work_ids[k],
                occurred_at=_utc(work_ts[k]),
                member_id=member_ids[int(work_idx[k])],
                workout_type=WORKOUT_TYPES[int(w_type[k])],
                duration_min=int(w_dur[k]),
            ),
        )
        for k in range(len(work_idx))
    ]
    events += [
        (
            buy_ts[k],
            PurchaseMade(
                event_id=buy_ids[k],
                occurred_at=_utc(buy_ts[k]),
                member_id=member_ids[int(buy_idx[k])],
                sku=sku_names[int(sku_choice[k])],
                amount_tax_incl=SKUS[sku_names[int(sku_choice[k])]],
                tax_rate=TAX_RATE,
            ),
        )
        for k in range(len(buy_idx))
    ]

    if cfg.gap_day is not None:
        gap = np.datetime64(cfg.gap_day, "D")
        events = [(t, e) for t, e in events if t.astype("datetime64[D]") != gap]

    events.sort(key=lambda pair: pair[0])
    _write_events(out_dir, [e for _, e in events], cfg, rng)
    if cfg.inject_bad_files:
        _write_bad_file(out_dir)
    return _kpis(cfg, opens_ts, opens_idx, work_ts, buy_ts, sku_names, sku_choice)


def _utc(ts: np.datetime64) -> datetime:
    return datetime.fromtimestamp(int(ts.astype("datetime64[s]").astype("int64")), tz=UTC)


def _write_events(
    out_dir: Path, events: list[object], cfg: GeneratorConfig, rng: np.random.Generator
) -> None:
    """Write everything once, then re-deliver a small share through a second writer."""
    with Writer(out_dir, max_rows=25_000) as writer:
        writer.write_many(events)  # type: ignore[arg-type]
    n_dup = int(len(events) * cfg.a_duplicate_rate)
    if n_dup:
        pick = rng.choice(len(events), size=n_dup, replace=False)
        with Writer(out_dir, max_rows=25_000) as retry_writer:
            retry_writer.write_many([events[int(i)] for i in pick])  # type: ignore[misc]


def _write_bad_file(out_dir: Path) -> None:
    """A stray file with a missing ``member_id`` (the landing contract must quarantine it)."""
    files = sorted((out_dir / "app_opened").glob("dt=*/part-*.parquet"))
    source = files[len(files) // 2]
    table = pq.read_table(source)
    position = table.schema.get_field_index("member_id")
    values = table.column("member_id").to_pylist()
    values[0] = None
    field = table.schema.field(position).with_nullable(True)
    table = table.set_column(position, field, pa.array(values, type=field.type))
    pq.write_table(table, source.with_name("part-bad.parquet"))


def _kpis(
    cfg: GeneratorConfig,
    opens_ts: np.ndarray,
    opens_idx: np.ndarray,
    work_ts: np.ndarray,
    buy_ts: np.ndarray,
    sku_names: list[str],
    sku_choice: np.ndarray,
) -> pd.DataFrame:
    """A's own KPIs, computed the way A's dashboards define them."""
    months = month_starts(cfg)
    start = np.datetime64(cfg.start, "D")

    def day_of(ts: np.ndarray) -> np.ndarray:
        return (ts.astype("datetime64[D]") - start).astype(int)

    ends = [(month_end(m) - cfg.start).days for m in months]
    starts = [(m - cfg.start).days for m in months]
    open_day, work_day = day_of(opens_ts), day_of(work_ts)
    buy_day = day_of(buy_ts)
    prices = np.array([SKUS[sku_names[int(k)]] for k in sku_choice], dtype=float)

    active = rolling_distinct(open_day, opens_idx, ends, 30)
    revenue = [
        float(prices[(buy_day >= s) & (buy_day <= e)].sum())
        for s, e in zip(starts, ends, strict=True)
    ]
    opened_in_month = rolling_distinct(open_day, opens_idx, ends, max(1, 28))
    per_user = []
    for s, e, users in zip(starts, ends, opened_in_month, strict=True):
        workouts = int(((work_day >= s) & (work_day <= e)).sum())
        per_user.append(workouts / users if users else 0.0)
    return pd.concat(
        [
            kpi_frame("A", "active_users",
                      "Distinct members who opened the app in the 30 days ending on the last "
                      "day of the month (a workout is not required).",
                      months, [float(a) for a in active]),
            kpi_frame("A", "iap_revenue",
                      "Sum of in-app purchases in the month, tax-inclusive, refunds not "
                      "deducted.", months, revenue),
            kpi_frame("A", "workouts_per_user",
                      "Workouts logged in the month divided by members who opened the app "
                      "in the 28 days ending on the last day of the month.", months, per_user),
        ],
        ignore_index=True,
    )  # fmt: skip
