"""Product C: the in-house gym back-office database, delivered as a CDC log.

Every change to a row arrives as one record ``(op, changed_at, lsn, <row values>)`` with
``op`` in ``I`` / ``U`` / ``D`` (what a Datastream-style replication would produce).
Deliberate inconsistencies of C, each normalized in silver:

* the price revision splits contracts into **two table generations** (FY2025 and FY2026)
  with different plan codes and columns; both are stored in different tables;
* the same row is updated many times, sometimes without any value changing;
* members and contracts can be deleted (``op = D``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import GeneratorConfig
from .common import instants, kpi_frame, month_end, month_starts
from .world import World, kana_variants, records, sample_days, span_bounds

FY25_PLANS: dict[str, tuple[str, int]] = {
    "STD": ("P100", 6600),
    "PRM": ("P200", 9900),
    "STU": ("P150", 4400),
    "FAM": ("P300", 5500),
}
FY26_PRICE_EX_TAX: dict[str, int] = {
    "P100": 6500,
    "P200": 9500,
    "P150": 4500,
    "P300": 5000,
    "P400": 5500,
}
FY26_TAX_RATE = 0.10
FACILITIES = tuple(f"F{i:02d}" for i in range(1, 9))


@dataclass
class _Contract:
    member_id: int
    generation: int  # 2025 or 2026
    plan: str
    start: date
    end: date | None
    end_kind: str | None  # "ended" | "migrated" | None
    guardian_member_id: int | None

    def fee_incl(self) -> int:
        if self.generation == 2025:
            return FY25_PLANS[self.plan][1]
        return int(round(FY26_PRICE_EX_TAX[self.plan] * (1 + FY26_TAX_RATE)))


def _ts(day: date, secs: int) -> pd.Timestamp:
    return pd.Timestamp(day, tz="UTC") + pd.Timedelta(seconds=secs)


def _jst_day_ts(rng: np.random.Generator, day: date) -> pd.Timestamp:
    """A UTC instant on JST calendar day ``day`` during opening hours."""
    return _ts(day, int(rng.integers(6 * 3600, 22 * 3600)) - 9 * 3600)


def _load_time(rng: np.random.Generator, cfg: GeneratorConfig, day: date) -> pd.Timestamp:
    """Rows that existed before the log began arrive in an initial load on the first day."""
    if day < cfg.start:
        return _ts(cfg.start, int(rng.integers(0, 3600)))
    return _jst_day_ts(rng, day)


def _after_load(
    events: list[tuple[pd.Timestamp, str, dict[str, Any]]],
) -> list[tuple[pd.Timestamp, str, dict[str, Any]]]:
    """Sort events; nothing may be logged before the row's own ``I`` record."""
    first = events[0][0]
    later = [(max(t, first + pd.Timedelta(seconds=60)), op, ch) for t, op, ch in events[1:]]
    return [events[0], *sorted(later, key=lambda e: e[0])]


def _phone_c(digits: str | None) -> str | None:
    if digits is None or pd.isna(digits):
        return None
    return f"{digits[:3]} {digits[3:7]} {digits[7:]}"


def generate_c(world: World, rng: np.random.Generator, out_dir: Path) -> pd.DataFrame:
    """Write C's CDC log under ``out_dir`` and return its self-reported KPIs."""
    cfg = world.cfg
    members = world.members("C")
    n = len(members)
    member_ids = 100_000 + np.arange(n)
    id_by_person = {int(p): int(member_ids[i]) for i, p in enumerate(members["person_id"])}
    email_by_person = dict(zip(world.persons["person_id"], world.persons["email"], strict=True))
    facilities = rng.integers(0, len(FACILITIES), size=n)
    delete_at: dict[int, pd.Timestamp] = {}
    member_rows: list[dict[str, Any]] = []

    for i, row in enumerate(records(members)):
        mid = int(member_ids[i])
        email = (
            None
            if pd.isna(row["email"])
            else f"{row['email']} "
            if rng.random() < 0.05
            else row["email"]
        )
        state: dict[str, Any] = {
            "member_id": mid,
            "name": row["name"],
            "name_kana": kana_variants(str(row["kana"]), int(row["kana_family_len"]))["plain"],
            "email": email,
            "phone": _phone_c(row["phone"]),
            "birth_date": row["birth_date"],
            "guardian_email": (
                str(email_by_person[int(row["guardian_person_id"])]) if row["is_child"] else None
            ),
            "medical_conditions": None if pd.isna(row["health_note"]) else str(row["health_note"]),
            "facility_id": FACILITIES[int(facilities[i])],
            "status": "active",
            "joined_on": row["joined_on"],
        }
        events: list[tuple[pd.Timestamp, str, dict[str, Any]]] = [
            (_load_time(rng, cfg, row["joined_on"]), "I", {})
        ]
        span_start = max(row["joined_on"], cfg.start)
        span_end = (
            row["left_on"]
            if row["left_on"] is not None and not pd.isna(row["left_on"])
            else cfg.end
        )
        span_days = max(1, (span_end - span_start).days)
        if rng.random() < 0.15:
            day = span_start + timedelta(days=int(rng.integers(0, span_days)))
            new_phone = f"090{int(rng.integers(10_000_000, 100_000_000))}"
            events.append((_jst_day_ts(rng, day), "U", {"phone": _phone_c(new_phone)}))
        for _ in range(int(rng.integers(0, 4)) if rng.random() < 0.3 else 0):
            day = span_start + timedelta(days=int(rng.integers(0, span_days)))
            events.append((_jst_day_ts(rng, day), "U", {}))  # a touch: no value changes
        if row["left_on"] is not None and not pd.isna(row["left_on"]):
            events.append((_jst_day_ts(rng, row["left_on"]), "U", {"status": "left"}))
        if rng.random() < 0.015:
            day = min(span_start + timedelta(days=1 + int(rng.integers(0, span_days))), cfg.end)
            t = _jst_day_ts(rng, day)
            delete_at[mid] = t
            events.append((t, "D", {}))
        events = _after_load(events)
        cut = delete_at.get(mid)
        if cut is not None:
            events = [e for e in events if e[0] <= cut]
        for t, op, change in events:
            state = {**state, **change}
            member_rows.append({"op": op, "changed_at": t, **state})

    contracts = _make_contracts(cfg, rng, members, member_ids, id_by_person)
    fy25_rows, fy26_rows = _contract_rows(cfg, rng, contracts, delete_at)
    visits = _visits(cfg, rng, members, member_ids, facilities)

    _write_cdc(out_dir, "members", pd.DataFrame(member_rows))
    _write_cdc(out_dir, "contracts_fy2025", pd.DataFrame(fy25_rows))
    _write_cdc(out_dir, "contracts_fy2026", pd.DataFrame(fy26_rows))
    _write_cdc(out_dir, "visits", visits, add_op=True)
    if cfg.inject_bad_files:
        _write_bad_file(out_dir, cfg)
    return _kpis(cfg, contracts, visits)


def _make_contracts(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    members: pd.DataFrame,
    member_ids: np.ndarray,
    id_by_person: dict[int, int],
) -> list[_Contract]:
    switch = cfg.fy2026_start
    plan_p = (0.5, 0.25, 0.25)
    out: list[_Contract] = []
    for i, row in enumerate(records(members)):
        mid = int(member_ids[i])
        left = None if row["left_on"] is None or pd.isna(row["left_on"]) else row["left_on"]
        guardian = id_by_person.get(int(row["guardian_person_id"])) if row["is_child"] else None
        plan = "FAM" if row["is_child"] else ("STD", "PRM", "STU")[int(rng.choice(3, p=plan_p))]
        if row["joined_on"] < switch:
            if left is not None and left < switch:
                out.append(_Contract(mid, 2025, plan, row["joined_on"], left, "ended", guardian))
                continue
            out.append(
                _Contract(mid, 2025, plan, row["joined_on"], switch - timedelta(days=1), "migrated",
                          guardian)
            )  # fmt: skip
            new_plan = FY25_PLANS[plan][0]
            out.append(_Contract(mid, 2026, new_plan, switch, left, "ended" if left else None,
                                 guardian))  # fmt: skip
        else:
            if row["is_child"]:
                new_plan = "P300"
            elif rng.random() < 0.2:
                new_plan = "P400"
            else:
                new_plan = FY25_PLANS[plan][0]
            out.append(
                _Contract(mid, 2026, new_plan, row["joined_on"], left, "ended" if left else None,
                          guardian)
            )  # fmt: skip
    return out


def _contract_rows(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    contracts: list[_Contract],
    delete_at: dict[int, pd.Timestamp],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fy25: list[dict[str, Any]] = []
    fy26: list[dict[str, Any]] = []
    for k, c in enumerate(contracts):
        cid = f"K{c.generation % 100}-{k:06d}"
        if c.generation == 2025:
            state: dict[str, Any] = {
                "contract_id": cid,
                "member_id": c.member_id,
                "plan_cd": c.plan,
                "monthly_fee": c.fee_incl(),
                "start_dt": c.start,
                "end_dt": None,
                "family_group_id": c.guardian_member_id,
                "status": "active",
            }
            end_fields = {"end_dt": c.end, "status": c.end_kind}
            sink = fy25
        else:
            state = {
                "contract_id": cid,
                "member_id": c.member_id,
                "plan_id": c.plan,
                "price_ex_tax": FY26_PRICE_EX_TAX[c.plan],
                "tax_rate": FY26_TAX_RATE,
                "effective_from": c.start,
                "effective_to": None,
                "payer_member_id": c.guardian_member_id,
                "billing_cycle": "monthly",
                "status": "active",
            }
            end_fields = {"effective_to": c.end, "status": c.end_kind}
            sink = fy26
        events: list[tuple[pd.Timestamp, str, dict[str, Any]]] = [
            (_load_time(rng, cfg, c.start), "I", {})
        ]
        span_end = c.end or cfg.end
        span_days = max(1, (span_end - max(c.start, cfg.start)).days)
        if rng.random() < 0.08:  # a freeze that is later lifted
            d1 = max(c.start, cfg.start) + timedelta(days=int(rng.integers(0, span_days)))
            events.append((_jst_day_ts(rng, d1), "U", {"status": "paused"}))
            events.append(
                (
                    _jst_day_ts(rng, d1 + timedelta(days=int(rng.integers(3, 30)))),
                    "U",
                    {"status": "active"},
                )  # fmt: skip
            )
        for _ in range(int(rng.integers(1, 4)) if rng.random() < 0.2 else 0):
            d = max(c.start, cfg.start) + timedelta(days=int(rng.integers(0, span_days)))
            events.append((_jst_day_ts(rng, d), "U", {}))  # a touch: no value changes
        if c.end is not None and c.end_kind is not None:
            events.append(
                (_jst_day_ts(rng, min(c.end + timedelta(days=1), cfg.end)), "U", end_fields)
            )
        cutoff = delete_at.get(c.member_id)
        events = _after_load(events)
        for t, op, change in events:
            if cutoff is not None and t > cutoff:
                continue
            state = {**state, **change}
            sink.append({"op": op, "changed_at": t, **state})
        if cutoff is not None:
            sink.append({"op": "D", "changed_at": cutoff, **state})
    if cfg.inject_unmapped_plan:
        fy26.append(
            {
                "op": "I",
                "changed_at": _ts(cfg.end, 3600),
                "contract_id": "K26-UNMAPPED",
                "member_id": 100_000,
                "plan_id": "P999",
                "price_ex_tax": 1000,
                "tax_rate": FY26_TAX_RATE,
                "effective_from": cfg.end,
                "effective_to": None,
                "payer_member_id": None,
                "billing_cycle": "monthly",
                "status": "active",
            }
        )
    return fy25, fy26


def _visits(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    members: pd.DataFrame,
    member_ids: np.ndarray,
    facilities: np.ndarray,
) -> pd.DataFrame:
    lo, hi = span_bounds(cfg, members["joined_on"], members["left_on"])
    months_active = (hi - lo + 1) / 30.4
    idx, off = sample_days(
        rng, lo, hi, rng.poisson(rng.gamma(1.6, 1.8, len(members)) * months_active), cfg.start, 0.8
    )
    ts = instants(rng, cfg.start, off)
    away = rng.random(len(idx)) < 0.2
    fac = np.where(away, rng.integers(0, len(FACILITIES), size=len(idx)), facilities[idx])
    frame = pd.DataFrame(
        {
            "visit_id": (np.arange(len(idx)) + 1).astype("int64"),
            "member_id": member_ids[idx].astype("int64"),
            "checked_in_at": pd.to_datetime(ts).tz_localize("UTC").astype("datetime64[us, UTC]"),
            "facility_id": [FACILITIES[int(f)] for f in fac],
        }
    )
    frame["changed_at"] = frame["checked_in_at"]
    return frame.sort_values("changed_at", kind="stable").reset_index(drop=True)


def _write_cdc(out_dir: Path, table: str, frame: pd.DataFrame, add_op: bool = False) -> None:
    """Write one CDC table as ``<table>/dt=<UTC date of changed_at>/part-000.parquet``."""
    if add_op:
        frame = frame.copy()
        frame.insert(0, "op", "I")
    frame = frame.sort_values("changed_at", kind="stable").reset_index(drop=True)
    frame["changed_at"] = frame["changed_at"].astype("datetime64[us, UTC]")
    frame.insert(2, "lsn", np.arange(1, len(frame) + 1, dtype="int64"))
    dts = frame["changed_at"].dt.strftime("%Y-%m-%d")
    for dt, part in frame.groupby(dts, sort=True):
        target = out_dir / table / f"dt={dt}"
        target.mkdir(parents=True, exist_ok=True)
        part.to_parquet(target / "part-000.parquet", index=False)


def _write_bad_file(out_dir: Path, cfg: GeneratorConfig) -> None:
    """A CDC file with an unknown ``op`` value (must be quarantined by the contract)."""
    target = out_dir / "visits" / f"dt={cfg.start.isoformat()}"
    target.mkdir(parents=True, exist_ok=True)
    bad = pd.DataFrame(
        {
            "op": ["X"],
            "changed_at": [_ts(cfg.start, 100)],
            "lsn": [999_999_999],
            "visit_id": [0],
            "member_id": [0],
            "checked_in_at": [_ts(cfg.start, 100)],
            "facility_id": ["F01"],
        }
    )
    bad.to_parquet(target / "part-bad.parquet", index=False)


def _kpis(cfg: GeneratorConfig, contracts: list[_Contract], visits: pd.DataFrame) -> pd.DataFrame:
    """C's own KPIs, from the contract intervals and the visits."""
    months = month_starts(cfg)
    vis_month = visits["checked_in_at"].dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m")
    active: list[float] = []
    churned: list[float] = []
    revenue: list[float] = []
    avg_visits: list[float] = []
    for m in months:
        end = month_end(m)
        live = [c for c in contracts if c.start <= end and (c.end is None or c.end >= end)]
        n_members = len({c.member_id for c in live})
        active.append(float(n_members))
        revenue.append(float(sum(c.fee_incl() for c in live)))
        churned.append(float(sum(1 for c in contracts if c.end is not None and m <= c.end <= end)))
        n_visits = int((vis_month == m.strftime("%Y-%m")).sum())
        avg_visits.append(n_visits / n_members if n_members else 0.0)
    return pd.concat(
        [
            kpi_frame("C", "active_members",
                      "Distinct members holding a contract on the last day of the month, "
                      "paused contracts included; every contract generation counts.",
                      months, active),
            kpi_frame("C", "churned_members",
                      "Contracts whose end date falls in the month. A migration to the new "
                      "price list closes the old contract and therefore counts as a churn.",
                      months, churned),
            kpi_frame("C", "monthly_revenue",
                      "Tax-inclusive monthly fee of all contracts alive on the last day of "
                      "the month.", months, revenue),
            kpi_frame("C", "avg_visits_per_member",
                      "Check-ins in the month (Tokyo time) divided by active_members.",
                      months, avg_visits),
        ],
        ignore_index=True,
    )  # fmt: skip
