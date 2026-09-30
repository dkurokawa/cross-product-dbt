"""The contract of every ``(source, dataset)`` pair."""

from __future__ import annotations

from dataclasses import dataclass

import pandera.pandas as pa

_TS = "datetime64[us, UTC]"


def _s(nullable: bool = False, **kw: object) -> pa.Column:
    return pa.Column(str, nullable=nullable, **kw)  # type: ignore[arg-type]


def _i(nullable: bool = False, ge: int | None = None) -> pa.Column:
    checks = [pa.Check.ge(ge)] if ge is not None else []
    return pa.Column(
        "Int64" if nullable else "int64", nullable=nullable, checks=checks, coerce=True
    )


def _ts(nullable: bool = False) -> pa.Column:
    return pa.Column(_TS, nullable=nullable, coerce=True)


def _naive_ts(nullable: bool = False) -> pa.Column:
    return pa.Column("datetime64[us]", nullable=nullable, coerce=True)


def _date(nullable: bool = False) -> pa.Column:
    return pa.Column(object, nullable=nullable)


def _pattern(regex: str, nullable: bool = False) -> pa.Column:
    return pa.Column(str, nullable=nullable, checks=pa.Check.str_matches(regex))


def _email(nullable: bool = False) -> pa.Column:
    return pa.Column(str, nullable=nullable, checks=pa.Check.str_contains("@"))


_LANDING_A = {
    "member_key": _s(True),
    "link_key": _s(True),
    "key_method": _s(True),
    "household_key": _s(True),
    "ingested_at": _ts(True),
}
_EVENT = {"event_id": _s(), "occurred_at": _ts(), "recorded_at": _ts()}
_CDC = {
    "op": pa.Column(str, checks=pa.Check.isin(["I", "U", "D"])),
    "changed_at": _ts(),
    "lsn": _i(ge=1),
}


@dataclass(frozen=True)
class Contract:
    """A pandera schema for one dataset, plus where its identity columns are."""

    source: str
    dataset: str
    schema: pa.DataFrameSchema


def _schema(columns: dict[str, pa.Column], strict: bool, **kw: object) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(columns, strict=strict, **kw)  # type: ignore[arg-type]


def _build() -> dict[tuple[str, str], Contract]:
    c: dict[tuple[str, str], pa.DataFrameSchema] = {}
    # --- A: eventlake events; extra columns are tolerated (schema evolution) ---------
    c["A", "member_registered"] = _schema(
        {
            **_EVENT,
            "member_id": _s(),
            "email": _email(nullable=True),
            "name": _s(),
            "name_kana": _s(True),
            "phone": _s(True),
            "birth_date": _date(),
            "guardian_member_id": _s(True),
            "guardian_email": _s(True),
            "health_notes": _s(True),
            **_LANDING_A,
        },
        strict=False,
    )
    c["A", "app_opened"] = _schema(
        {**_EVENT, "member_id": _s(), "ingested_at": _ts(True)}, strict=False
    )
    c["A", "workout_completed"] = _schema(
        {
            **_EVENT,
            "member_id": _s(),
            "workout_type": _s(),
            "duration_min": _i(ge=1),
            "ingested_at": _ts(True),
        },
        strict=False,
    )
    c["A", "purchase_made"] = _schema(
        {
            **_EVENT,
            "member_id": _s(),
            "sku": _s(),
            "amount_tax_incl": _i(ge=0),
            "tax_rate": pa.Column(float, checks=pa.Check.in_range(0, 1)),
            "ingested_at": _ts(True),
        },
        strict=False,
    )
    # --- B: full snapshots -------------------------------------------------------------
    c["B", "customers"] = _schema(
        {
            "tenant_id": _pattern(r"^T\d{3}$"),
            "member_no": _i(ge=1),
            "name": _s(),
            "name_kana": _s(True),
            "email": _email(nullable=True),
            "phone": _s(True),
            "created_at": _naive_ts(),
            "is_deleted": pa.Column(bool),
            "deleted_at": _naive_ts(True),
        },
        strict=True,
        unique=["tenant_id", "member_no"],
    )
    c["B", "bookings"] = _schema(
        {
            "tenant_id": _pattern(r"^T\d{3}$"),
            "booking_no": _i(ge=1),
            "member_no": _i(ge=1),
            "class_start_at": _naive_ts(),
            "created_at": _naive_ts(),
            "status": pa.Column(
                str, checks=pa.Check.isin(["booked", "attended", "cancelled", "no_show"])
            ),
            "price_ex_tax": _i(ge=0),
            "tax_rate": pa.Column(float, checks=pa.Check.in_range(0, 1)),
            "is_deleted": pa.Column(bool),
        },
        strict=True,
        unique=["tenant_id", "booking_no"],
    )
    # --- C: CDC tables -----------------------------------------------------------------
    c["C", "members"] = _schema(
        {
            **_CDC,
            "member_id": _i(ge=1),
            "name": _s(),
            "name_kana": _s(True),
            "email": _s(True),
            "phone": _s(True),
            "birth_date": _date(),
            "guardian_email": _s(True),
            "medical_conditions": _s(True),
            "facility_id": _s(),
            "status": _s(),
            "joined_on": _date(),
        },
        strict=True,
    )
    c["C", "contracts_fy2025"] = _schema(
        {
            **_CDC,
            "contract_id": _s(),
            "member_id": _i(ge=1),
            "plan_cd": _s(),
            "monthly_fee": _i(ge=0),
            "start_dt": _date(),
            "end_dt": _date(True),
            "family_group_id": _i(True),
            "status": _s(),
        },
        strict=True,
    )
    c["C", "contracts_fy2026"] = _schema(
        {
            **_CDC,
            "contract_id": _s(),
            "member_id": _i(ge=1),
            "plan_id": _s(),
            "price_ex_tax": _i(ge=0),
            "tax_rate": pa.Column(float, checks=pa.Check.in_range(0, 1)),
            "effective_from": _date(),
            "effective_to": _date(True),
            "payer_member_id": _i(True),
            "billing_cycle": _s(),
            "status": _s(),
        },
        strict=True,
    )
    c["C", "visits"] = _schema(
        {
            **_CDC,
            "visit_id": _i(ge=0),
            "member_id": _i(ge=0),
            "checked_in_at": _ts(),
            "facility_id": _s(),
        },
        strict=True,
    )
    # --- D: CSV (all values arrive as text) ---------------------------------------------
    c["D", "invoices"] = _schema(
        {
            "請求番号": _pattern(r"^INV\d{7}$"),
            "請求日": _pattern(r"^\d{4}/\d{2}/\d{2}$"),
            "氏名": _s(),
            "氏名カナ": _s(),
            "電話番号": _s(),
            "生年月日": _pattern(r"^\d{4}/\d{2}/\d{2}$"),
            "プランコード": _s(),
            "請求金額（税抜）": _pattern(r"^\d+$"),
            "消費税率": _pattern(r"^\d+%$"),
            "入金状況": _s(),
        },
        strict=True,
    )
    c["D", "refunds"] = _schema(
        {
            "返金番号": _pattern(r"^RF\d{6}$"),
            "元請求番号": _pattern(r"^INV\d{7}$"),
            "返金日": _pattern(r"^\d{4}/\d{2}/\d{2}$"),
            "返金額（税抜）": _pattern(r"^\d+$"),
        },
        strict=True,
    )
    # --- E: the sheet after its column names were canonicalized ---------------------------
    c["E", "accounts"] = _schema(
        {
            "company_name": _s(),
            "contact_name": _s(True),
            "seats": _s(True),
            "monthly_fee_yen": _s(True),
            "contract_status": _s(),
            "sales_owner": _s(True),
        },
        strict=True,
    )
    # --- self-reported KPIs of every product -----------------------------------------------
    c["KPI", "reported_kpis"] = _schema(
        {
            "product": pa.Column(str, checks=pa.Check.isin(["A", "B", "C", "D", "E"])),
            "reported_as": _s(),
            "period_month": _date(),
            "value": pa.Column(float),
            "definition": _s(),
        },
        strict=True,
    )
    return {k: Contract(k[0], k[1], v) for k, v in c.items()}


CONTRACTS: dict[tuple[str, str], Contract] = _build()


def get_contract(source: str, dataset: str) -> Contract:
    """Look up the contract for a dataset (``KeyError`` if there is none)."""
    return CONTRACTS[source, dataset]
