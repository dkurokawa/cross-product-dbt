"""Helpers shared by the per-source generators."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..config import GeneratorConfig, add_months

KPI_COLUMNS = ["product", "reported_as", "period_month", "value", "definition"]


def month_starts(cfg: GeneratorConfig) -> list[date]:
    """First day of each month in the generated period."""
    return [add_months(cfg.start, i) for i in range(cfg.months)]


def month_end(month_start: date) -> date:
    """Last day of the month starting at ``month_start``."""
    return add_months(month_start, 1) - timedelta(days=1)


def instants(rng: np.random.Generator, start: date, offsets: np.ndarray) -> np.ndarray:
    """UTC instants for events on JST day ``start + offset`` between 06:00 and 22:00 JST.

    Returns ``datetime64[s]`` (naive, in UTC). Because JST is UTC+9 the UTC date of an
    event is often the day before its local date: partitions and local days differ.
    """
    secs = rng.integers(6 * 3600, 22 * 3600, size=len(offsets))
    base = np.datetime64(start, "D") + offsets.astype("timedelta64[D]")
    result: np.ndarray = (
        base.astype("datetime64[s]") + secs.astype("timedelta64[s]") - np.timedelta64(9 * 3600, "s")
    )
    return result


def random_uuids(rng: np.random.Generator, n: int) -> list[uuid.UUID]:
    """``n`` deterministic (seeded) version-4-shaped UUIDs."""
    raw = rng.integers(0, 2**63, size=(n, 2), dtype=np.uint64)
    return [
        uuid.UUID(int=(int(hi) << 64 | int(lo)) & ~(0xF000 << 64) | (0x4000 << 64), version=4)
        for hi, lo in raw
    ]


def kpi_frame(
    product: str,
    reported_as: str,
    definition: str,
    months: list[date],
    values: list[float],
) -> pd.DataFrame:
    """One self-reported KPI as rows ``(product, reported_as, period_month, value, definition)``."""
    return pd.DataFrame(
        {
            "product": product,
            "reported_as": reported_as,
            "period_month": months,
            "value": [float(v) for v in values],
            "definition": definition,
        },
        columns=KPI_COLUMNS,
    )


def rolling_distinct(
    day_index: np.ndarray, member: np.ndarray, month_ends: list[int], window: int
) -> list[int]:
    """Distinct members with an event in the ``window`` days ending on each month end."""
    out: list[int] = []
    for end in month_ends:
        mask = (day_index > end - window) & (day_index <= end)
        out.append(int(len(np.unique(member[mask]))))
    return out
