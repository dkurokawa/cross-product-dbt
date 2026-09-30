"""Product D: an acquired billing package, delivered as daily CSV files.

Deliberate inconsistencies of D, each normalized in landing/bronze/silver: the files are
Shift_JIS (Windows-31J) with Japanese headers; dates are ``YYYY/MM/DD``; the plan codes are
D's own (``M1``, ``M2``, ``QK``, ``YR``); amounts are tax-exclusive; refunds arrive in a
separate file; and a customer is identified only by kana name + phone number (no member id),
written in half-width katakana and full-width digits.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import GeneratorConfig, add_months
from .common import kpi_frame, month_end, month_starts
from .world import World, kana_variants, records, to_fullwidth_digits

ENCODING = "cp932"
INVOICE_HEADERS = [
    "請求番号", "請求日", "氏名", "氏名カナ", "電話番号", "生年月日",
    "プランコード", "請求金額（税抜）", "消費税率", "入金状況",
]  # fmt: skip
REFUND_HEADERS = ["返金番号", "元請求番号", "返金日", "返金額（税抜）"]

#: code -> (tax-exclusive amount, months between invoices)
PLANS: dict[str, tuple[int, int]] = {
    "M1": (5000, 1),
    "M2": (8000, 1),
    "QK": (14000, 3),
    "YR": (54000, 12),
}
TAX_RATE = 0.10


def _slash(day: date) -> str:
    return f"{day.year:04d}/{day.month:02d}/{day.day:02d}"


def generate_d(world: World, rng: np.random.Generator, out_dir: Path) -> pd.DataFrame:
    """Write D's CSV files under ``out_dir`` and return its self-reported KPIs."""
    cfg = world.cfg
    members = world.members("D")
    codes = list(PLANS)
    plan_choice = rng.choice(len(codes), size=len(members), p=[0.45, 0.25, 0.2, 0.1])
    rows: list[dict[str, object]] = []
    for i, m in enumerate(records(members)):
        code = codes[int(plan_choice[i])]
        amount, every = PLANS[code]
        billing_day = int(rng.integers(1, 29))
        anchor = max(m["joined_on"], cfg.start)
        first_month = anchor.replace(day=1)
        if first_month.replace(day=billing_day) < anchor:
            first_month = add_months(first_month, 1)
        left = None if m["left_on"] is None or pd.isna(m["left_on"]) else m["left_on"]
        k = 0
        while True:
            month = add_months(first_month, k * every)
            day = month.replace(day=billing_day)
            if day > cfg.end or (left is not None and day > left):
                break
            variants = kana_variants(str(m["kana"]), int(m["kana_family_len"]))
            rows.append(
                {
                    "person": i,
                    "invoice_date": day,
                    "name": str(m["name"]).replace(" ", "　"),
                    "kana": variants["halfwidth"],
                    "phone": to_fullwidth_digits(
                        f"{m['phone'][:3]}-{m['phone'][3:7]}-{m['phone'][7:]}"
                    ),
                    "birth": _slash(m["birth_date"]),
                    "plan": code,
                    "amount": amount,
                    "paid": "入金済" if rng.random() < 0.95 else "未入金",
                }
            )
            k += 1
    invoices = pd.DataFrame(rows).sort_values(["invoice_date", "person"], kind="stable")
    invoices = invoices.reset_index(drop=True)
    invoices["invoice_no"] = [f"INV{i + 1:07d}" for i in range(len(invoices))]

    refund_mask = rng.random(len(invoices)) < 0.04
    refunds = invoices[refund_mask].copy()
    refunds["refund_date"] = [
        d + timedelta(days=int(rng.integers(1, 21))) for d in refunds["invoice_date"]
    ]
    refunds = refunds[refunds["refund_date"] <= cfg.end].copy()
    refunds["refund_amount"] = np.where(rng.random(len(refunds)) < 0.6, refunds["amount"],
                                        refunds["amount"] // 2)  # fmt: skip
    refunds = refunds.sort_values("refund_date", kind="stable").reset_index(drop=True)
    refunds["refund_no"] = [f"RF{i + 1:06d}" for i in range(len(refunds))]

    _write_invoices(out_dir, invoices)
    _write_refunds(out_dir, refunds)
    if cfg.inject_bad_files:
        _write_bad_file(out_dir, invoices)
    return _kpis(cfg, invoices, refunds)


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding=ENCODING)


def _write_invoices(out_dir: Path, invoices: pd.DataFrame) -> None:
    for day, part in invoices.groupby("invoice_date", sort=True):
        assert isinstance(day, date)
        frame = pd.DataFrame(
            {
                "請求番号": part["invoice_no"],
                "請求日": [_slash(d) for d in part["invoice_date"]],
                "氏名": part["name"],
                "氏名カナ": part["kana"],
                "電話番号": part["phone"],
                "生年月日": part["birth"],
                "プランコード": part["plan"],
                "請求金額（税抜）": part["amount"],
                "消費税率": f"{int(TAX_RATE * 100)}%",
                "入金状況": part["paid"],
            },
            columns=INVOICE_HEADERS,
        )
        _write_csv(
            out_dir / "invoices" / f"dt={day.isoformat()}" / f"invoices_{day:%Y%m%d}.csv", frame
        )


def _write_refunds(out_dir: Path, refunds: pd.DataFrame) -> None:
    for day, part in refunds.groupby("refund_date", sort=True):
        assert isinstance(day, date)
        frame = pd.DataFrame(
            {
                "返金番号": part["refund_no"],
                "元請求番号": part["invoice_no"],
                "返金日": [_slash(d) for d in part["refund_date"]],
                "返金額（税抜）": part["refund_amount"],
            },
            columns=REFUND_HEADERS,
        )
        _write_csv(
            out_dir / "refunds" / f"dt={day.isoformat()}" / f"refunds_{day:%Y%m%d}.csv", frame
        )


def _write_bad_file(out_dir: Path, invoices: pd.DataFrame) -> None:
    """A re-sent file whose amount header was renamed by the vendor (must be quarantined)."""
    day = invoices["invoice_date"].iloc[len(invoices) // 2]
    part = invoices[invoices["invoice_date"] == day].head(3)
    frame = pd.DataFrame(
        {
            "請求番号": part["invoice_no"],
            "請求日": [_slash(d) for d in part["invoice_date"]],
            "請求額": part["amount"],
        }
    )
    _write_csv(
        out_dir / "invoices" / f"dt={day.isoformat()}" / f"invoices_{day:%Y%m%d}_resend.csv", frame
    )


def _kpis(cfg: GeneratorConfig, invoices: pd.DataFrame, refunds: pd.DataFrame) -> pd.DataFrame:
    """D's own KPIs."""
    months = month_starts(cfg)
    inv_date = pd.to_datetime(invoices["invoice_date"])
    ref_date = pd.to_datetime(refunds["refund_date"])
    billed: list[float] = []
    net: list[float] = []
    cancelled: list[float] = []
    for m in months:
        start, end = pd.Timestamp(m), pd.Timestamp(month_end(m))
        in_month = (inv_date >= start) & (inv_date <= end)
        billed.append(float(invoices.loc[in_month, "person"].nunique()))
        refunded = refunds.loc[(ref_date >= start) & (ref_date <= end), "refund_amount"].sum()
        net.append(float(invoices.loc[in_month, "amount"].sum() - refunded))
        seen = invoices.loc[inv_date <= end, "person"].nunique()
        recent = invoices.loc[
            (inv_date > end - pd.Timedelta(days=60)) & (inv_date <= end), "person"
        ]
        cancelled.append(float(seen - recent.nunique()))
    return pd.concat(
        [
            kpi_frame("D", "billed_customers",
                      "Distinct customers invoiced in the month (identified by kana name and "
                      "phone number).", months, billed),
            kpi_frame("D", "net_sales",
                      "Tax-exclusive invoice amounts of the month minus tax-exclusive refunds "
                      "issued in the month.", months, net),
            kpi_frame("D", "cancelled_customers",
                      "Customers invoiced before but not in the 60 days ending on the last day "
                      "of the month (quarterly and annual plans look cancelled between "
                      "invoices).", months, cancelled),
        ],
        ignore_index=True,
    )  # fmt: skip
