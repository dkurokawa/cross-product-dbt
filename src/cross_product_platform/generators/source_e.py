"""Product E: the corporate-sales spreadsheet, exported to one CSV per month.

Deliberate inconsistencies of E, normalized at landing (column names) and in bronze
(values): the column *names* drift from month to month (``会社名`` / ``企業名`` ...), and the
numeric columns contain ``—`` placeholders, full-width digits and thousands separators.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .common import kpi_frame, month_starts
from .world import World, to_fullwidth_digits

#: Two spellings of the header row that the sales team has used.
HEADER_DIALECTS: tuple[dict[str, str], ...] = (
    {
        "company_name": "会社名",
        "contact_name": "担当者",
        "seats": "契約人数",
        "monthly_fee_yen": "月額料金",
        "contract_status": "ステータス",
        "sales_owner": "営業担当",
    },
    {
        "company_name": "企業名",
        "contact_name": "担当者名",
        "seats": "利用人数",
        "monthly_fee_yen": "月額(円)",
        "contract_status": "契約状況",
        "sales_owner": "営業担当者",
    },
)
_SYLLABLES = tuple("アカサタナハマヤラミシトノロケルゾフェニクレーモ")
_TRADES = ("商事", "工業", "フーズ", "システムズ", "運輸", "建設", "薬品", "電機")
_LEGAL = ("株式会社{}", "{}株式会社", "(株){}", "{}")
_OWNERS = ("Tanaka", "Suzuki", "Ito", "Watanabe", "Yamamoto")
FEE_PER_SEAT = 3000


def _messy_number(value: int, rng: np.random.Generator) -> str:
    roll = rng.random()
    if roll < 0.10:
        return "—"
    if roll < 0.25:
        return to_fullwidth_digits(str(value))
    if roll < 0.35:
        return f"{value:,}"
    return str(value)


def generate_e(world: World, rng: np.random.Generator, out_dir: Path) -> pd.DataFrame:
    """Write E's monthly CSVs under ``out_dir`` and return its self-reported KPIs."""
    cfg = world.cfg
    n = cfg.n_accounts
    months = month_starts(cfg)
    names = [
        "".join(rng.choice(_SYLLABLES, size=3)) + _TRADES[int(rng.integers(len(_TRADES)))]
        for _ in range(n)
    ]
    seats = rng.integers(5, 200, size=n)
    contact = [f"担当{int(rng.integers(1, 500)):03d}" for _ in range(n)]
    owner = [_OWNERS[int(rng.integers(len(_OWNERS)))] for _ in range(n)]
    first = rng.integers(-6, cfg.months, size=n)
    last = np.where(
        rng.random(n) < 0.15, first + rng.integers(1, max(2, cfg.months), size=n), 10_000
    )
    dialect_of_month = rng.integers(0, len(HEADER_DIALECTS), size=cfg.months)

    seat_totals: list[float] = []
    fee_totals: list[float] = []
    for mi, month in enumerate(months):
        dialect = HEADER_DIALECTS[int(dialect_of_month[mi])]
        rows: list[dict[str, str]] = []
        seat_sum = fee_sum = 0
        for a in range(n):
            if not first[a] <= mi <= last[a]:
                continue
            churn_month = mi == last[a]
            fee = int(seats[a]) * FEE_PER_SEAT
            seat_text, fee_text = _messy_number(int(seats[a]), rng), _messy_number(fee, rng)
            if not churn_month:  # the sheet's own totals: a "—" cell counts as zero
                seat_sum += 0 if seat_text == "—" else int(seats[a])
                fee_sum += 0 if fee_text == "—" else fee
            legal = _LEGAL[int(rng.integers(len(_LEGAL)))]
            rows.append(
                {
                    dialect["company_name"]: legal.format(names[a]),
                    dialect["contact_name"]: contact[a],
                    dialect["seats"]: seat_text,
                    dialect["monthly_fee_yen"]: fee_text,
                    dialect["contract_status"]: "解約" if churn_month else "契約中",
                    dialect["sales_owner"]: owner[a],
                }
            )
        seat_totals.append(float(seat_sum))
        fee_totals.append(float(fee_sum))
        month_dir = out_dir / "accounts" / f"month={month:%Y-%m}"
        month_dir.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(rows, columns=list(dialect.values()))
        frame.to_csv(month_dir / f"accounts_{month:%Y%m}.csv", index=False, encoding="utf-8-sig")
        if cfg.inject_bad_files and mi == cfg.months // 2:
            bad = frame.head(5).assign(備考2="unmapped extra column")
            bad.to_csv(
                month_dir / f"accounts_{month:%Y%m}_v2.csv", index=False, encoding="utf-8-sig"
            )
    return _kpis(months, seat_totals, fee_totals)


def _kpis(months: list[date], seats: list[float], fees: list[float]) -> pd.DataFrame:
    """E's own KPIs: the column totals of the sheet."""
    return pd.concat(
        [
            kpi_frame("E", "seats_under_contract",
                      "Sum of the seats column of contracts not being cancelled this month; "
                      "a placeholder cell counts as zero.", months, seats),
            kpi_frame("E", "mrr",
                      "Sum of the monthly-fee column (tax-exclusive) of contracts not being "
                      "cancelled this month; a placeholder cell counts as zero.",
                      months, fees),
        ],
        ignore_index=True,
    )  # fmt: skip
