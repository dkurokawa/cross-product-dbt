"""Generator configuration.

Everything a run depends on lives in one frozen dataclass so that a run is
reproducible from ``(config, salt)`` alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


def add_months(d: date, months: int) -> date:
    """Return ``d`` moved forward by ``months`` calendar months (day clamped to 1)."""
    total = d.year * 12 + (d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


@dataclass(frozen=True)
class GeneratorConfig:
    """Parameters of the synthetic-data generators.

    All data is synthetic; nothing here refers to real people or organizations.
    """

    seed: int = 42
    #: Number of distinct people (adults and children) in the synthetic world.
    n_persons: int = 5000
    #: First day of the generated period (must be the first of a month).
    start: date = date(2025, 10, 1)
    #: Length of the period in months.
    months: int = 12
    #: Probability that a person also appears in a second product.
    overlap: float = 0.3
    #: If set, every product-A event on this UTC day is dropped (anomaly-test input).
    gap_day: date | None = None
    #: Add one extra malformed file per source that the landing step must quarantine.
    inject_bad_files: bool = True
    #: Add one contract row whose plan code is missing from the plan mapping seed.
    inject_unmapped_plan: bool = False
    #: Product B is delivered as full snapshots every N days (1 = daily).
    b_snapshot_every_days: int = 7
    n_tenants: int = 30
    n_accounts: int = 150
    #: Fraction of product-A events that are re-delivered by a second writer.
    a_duplicate_rate: float = 0.004

    @property
    def end(self) -> date:
        """Last day (inclusive) of the generated period."""
        return add_months(self.start, self.months) - timedelta(days=1)

    @property
    def fy2026_start(self) -> date:
        """Day the FY2026 price list (and contract table) takes over, within or after the period."""
        return date(2026, 4, 1)
