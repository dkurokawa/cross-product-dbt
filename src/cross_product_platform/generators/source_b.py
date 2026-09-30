"""Product B: a reservation SaaS for independent studios (multi-tenant).

Delivered as full-table snapshots of ``customers`` and ``bookings`` (what an AWS DMS full
load would produce). Deliberate inconsistencies of B, each normalized in bronze/silver:
a customer is identified by ``(tenant_id, member_no)`` where ``member_no`` is a running
number *inside the tenant*; timestamps are naive JST; prices are tax-exclusive; rows are
deleted logically (``is_deleted``) and keep appearing in later snapshots.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import GeneratorConfig
from .common import kpi_frame, month_end, month_starts
from .world import World, kana_variants, sample_days, span_bounds

PRICES = (2000, 2500, 3000, 4500)
TAX_RATE = 0.10
_STATUS = ("attended", "cancelled", "no_show")


def snapshot_dates(cfg: GeneratorConfig) -> list[date]:
    """Snapshot days: every ``b_snapshot_every_days`` days, always including the last day."""
    every = max(1, cfg.b_snapshot_every_days)
    days = [
        cfg.start + timedelta(days=i)
        for i in range(every - 1, (cfg.end - cfg.start).days + 1, every)
    ]
    if not days or days[-1] != cfg.end:
        days.append(cfg.end)
    return days


def _naive_jst(day: date, seconds: int) -> pd.Timestamp:
    return pd.Timestamp(day) + pd.Timedelta(seconds=seconds)


def _messy_email(email: str, rng: np.random.Generator) -> str:
    """Studio staff type e-mails by hand: mixed case and stray spaces."""
    roll = rng.random()
    if roll < 0.25:
        return email.upper()
    if roll < 0.35:
        return f" {email} "
    return email


def generate_b(world: World, rng: np.random.Generator, out_dir: Path) -> pd.DataFrame:
    """Write B's snapshots under ``out_dir`` and return its self-reported KPIs."""
    cfg = world.cfg
    members = world.members("B")
    n = len(members)
    tenants = rng.integers(1, cfg.n_tenants + 1, size=n)
    order = np.argsort(pd.to_datetime(members["joined_on"]).to_numpy(), kind="stable")
    member_no = np.zeros(n, dtype=int)
    counters: dict[int, int] = {}
    for i in order:
        counters[int(tenants[i])] = counters.get(int(tenants[i]), 0) + 1
        member_no[i] = counters[int(tenants[i])]
    lo, hi = span_bounds(cfg, members["joined_on"], members["left_on"])
    months_active = (hi - lo + 1) / 30.4

    join_off = (pd.to_datetime(members["joined_on"]) - pd.Timestamp(cfg.start)).dt.days.to_numpy()
    join_secs = rng.integers(9 * 3600, 20 * 3600, size=n)
    customers = pd.DataFrame(
        {
            "tenant_id": [f"T{t:03d}" for t in tenants],
            "member_no": member_no.astype("int64"),
            "name": members["name"].to_numpy(),
            "name_kana": [
                kana_variants(str(k), int(f))["spaced"]
                for k, f in zip(members["kana"], members["kana_family_len"], strict=True)
            ],
            "email": [_messy_email(str(e), rng) for e in members["email"]],
            "phone": members["phone"].to_numpy(),
            "created_at": [
                _naive_jst(cfg.start + timedelta(days=int(o)), int(s))
                for o, s in zip(join_off, join_secs, strict=True)
            ],
            "left_on": members["left_on"].to_numpy(),
        }
    )

    # --- bookings ------------------------------------------------------------------
    idx, class_off = sample_days(
        rng, lo, hi, rng.poisson(rng.gamma(1.8, 1.7, n) * months_active), cfg.start, 0.9
    )
    lead = rng.integers(0, 15, size=len(idx))
    created_off = np.maximum(lo[idx], class_off - lead)
    roll = rng.random(len(idx))
    status_ix = np.where(roll < 0.83, 0, np.where(roll < 0.95, 1, 2))
    cancel_gap = np.minimum(class_off - created_off, rng.integers(0, 8, size=len(idx)))
    resolved_off = np.where(status_ix == 1, created_off + cancel_gap, class_off)
    class_secs = rng.integers(7 * 3600, 21 * 3600, size=len(idx))
    created_secs = rng.integers(0, 86400, size=len(idx))
    tenant_price = {t: PRICES[t % len(PRICES)] for t in range(1, cfg.n_tenants + 1)}
    bookings = pd.DataFrame(
        {
            "tenant_id": customers["tenant_id"].to_numpy()[idx],
            "member_no": member_no[idx].astype("int64"),
            "class_start_at": [
                _naive_jst(cfg.start + timedelta(days=int(o)), int(s))
                for o, s in zip(class_off, class_secs, strict=True)
            ],
            "created_at": [
                _naive_jst(cfg.start + timedelta(days=int(o)), int(s))
                for o, s in zip(created_off, created_secs, strict=True)
            ],
            "final_status": [_STATUS[int(k)] for k in status_ix],
            "resolved_on": [cfg.start + timedelta(days=int(o)) for o in resolved_off],
            "price_ex_tax": np.array(
                [tenant_price[int(t[1:])] for t in customers["tenant_id"].to_numpy()[idx]],
                dtype="int64",
            ),
        }
    )
    bookings["booking_no"] = (bookings.groupby("tenant_id").cumcount() + 1).astype("int64")
    bookings["tax_rate"] = TAX_RATE

    snaps = snapshot_dates(cfg)
    for snap in snaps:
        _write_snapshot(out_dir, snap, customers, bookings)
    if cfg.inject_bad_files:
        _write_bad_file(out_dir, snaps[len(snaps) // 2], bookings)
    return _kpis(cfg, bookings)


def _write_snapshot(
    out_dir: Path, snap: date, customers: pd.DataFrame, bookings: pd.DataFrame
) -> None:
    cutoff = pd.Timestamp(snap) + pd.Timedelta(days=1)
    c = customers[customers["created_at"] < cutoff].copy()
    left = pd.to_datetime(c["left_on"])
    gone = left.notna() & (left <= pd.Timestamp(snap))
    c["is_deleted"] = gone.to_numpy()
    c["deleted_at"] = left.where(gone) + pd.Timedelta(hours=3)
    c = c.drop(columns=["left_on"])

    b = bookings[bookings["created_at"] < cutoff].copy()
    resolved = pd.to_datetime(b["resolved_on"])
    done = resolved <= pd.Timestamp(snap)
    b["status"] = np.where(done, b["final_status"], "booked")
    b["is_deleted"] = False
    b = b.drop(columns=["final_status", "resolved_on"])

    for name, frame in (("customers", c), ("bookings", b)):
        target = out_dir / name / f"dt={snap.isoformat()}"
        target.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target / "snapshot.parquet", index=False)


def _write_bad_file(out_dir: Path, snap: date, bookings: pd.DataFrame) -> None:
    """A re-sent bookings file that lost a required column (must be quarantined)."""
    target = out_dir / "bookings" / f"dt={snap.isoformat()}"
    bad = bookings.head(20).drop(columns=["price_ex_tax", "final_status", "resolved_on"])
    bad.to_parquet(target / "snapshot_resend.parquet", index=False)


def _kpis(cfg: GeneratorConfig, bookings: pd.DataFrame) -> pd.DataFrame:
    """B's own KPIs (the definitions are in each row's ``definition`` column)."""
    months = month_starts(cfg)
    who = bookings["tenant_id"] + "/" + bookings["member_no"].astype(str)
    created = pd.to_datetime(bookings["created_at"]).dt.normalize()
    cls = pd.to_datetime(bookings["class_start_at"]).dt.normalize()
    active_28: list[float] = []
    revenue: list[float] = []
    per_customer: list[float] = []
    lost: list[float] = []
    for m in months:
        end = pd.Timestamp(month_end(m))
        start = pd.Timestamp(m)
        in28 = (created > end - pd.Timedelta(days=28)) & (created <= end)
        n_active = who[in28].nunique()
        active_28.append(float(n_active))
        in_month = (cls >= start) & (cls <= end) & (bookings["final_status"] != "cancelled")
        revenue.append(float(bookings.loc[in_month, "price_ex_tax"].sum()))
        made = ((created >= start) & (created <= end)).sum()
        per_customer.append(float(made / n_active) if n_active else 0.0)
        seen = who[created <= end].nunique()
        in60 = (created > end - pd.Timedelta(days=60)) & (created <= end)
        lost.append(float(seen - who[in60].nunique()))
    return pd.concat(
        [
            kpi_frame("B", "active_customers",
                      "Distinct customers who created a booking in the 28 days ending on the "
                      "last day of the month (attendance not required, cancellations count).",
                      months, active_28),
            kpi_frame("B", "booking_revenue",
                      "Tax-exclusive price of bookings whose class falls in the month, "
                      "cancelled bookings excluded, no-shows included.", months, revenue),
            kpi_frame("B", "bookings_per_customer",
                      "Bookings created in the month divided by active_customers.",
                      months, per_customer),
            kpi_frame("B", "lost_customers",
                      "Customers with at least one booking who created none in the 60 days "
                      "ending on the last day of the month.", months, lost),
        ],
        ignore_index=True,
    )  # fmt: skip
