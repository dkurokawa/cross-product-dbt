from pathlib import Path

import pandas as pd
import pytest

from cross_product_platform.contracts import ContractViolation, get_contract, validate_frame
from cross_product_platform.contracts.aliases import canonicalize_columns, load_aliases


def _kpi_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product": ["A"],
            "reported_as": ["active_users"],
            "period_month": [pd.Timestamp("2026-01-01").date()],
            "value": [1.0],
            "definition": ["x"],
        }
    )


def test_valid_frame_passes() -> None:
    out = validate_frame(get_contract("KPI", "reported_kpis"), _kpi_frame())
    assert len(out) == 1


def test_violation_reports_columns_and_counts_but_no_values() -> None:
    frame = _kpi_frame().assign(product="SECRET-VALUE-Z")
    with pytest.raises(ContractViolation) as err:
        validate_frame(get_contract("KPI", "reported_kpis"), frame)
    assert "SECRET-VALUE-Z" not in repr(err.value.details)
    (detail,) = err.value.details
    assert detail["column"] == "product"
    assert detail["rows"] == 1
    assert "1 contract violation" in str(err.value)


def test_missing_and_unexpected_columns_are_named() -> None:
    contract = get_contract("KPI", "reported_kpis")
    with pytest.raises(ContractViolation) as missing:
        validate_frame(contract, _kpi_frame().drop(columns=["value"]))
    assert missing.value.details[0]["column_name"] == ["value"]
    with pytest.raises(ContractViolation) as extra:
        validate_frame(contract, _kpi_frame().assign(surprise=1))
    assert extra.value.details[0]["column_name"] == ["surprise"]


def test_aliases_map_every_spelling_to_one_canonical_name() -> None:
    lookup = load_aliases()
    frame = pd.DataFrame(
        columns=["企業名", "担当者名", "利用人数", "月額（円）", "契約状況", "営業担当者"]
    )
    renamed = canonicalize_columns(frame, lookup)
    assert list(renamed.columns) == [
        "company_name",
        "contact_name",
        "seats",
        "monthly_fee_yen",
        "contract_status",
        "sales_owner",
    ]


def test_unknown_header_is_rejected_not_guessed() -> None:
    frame = pd.DataFrame(columns=["会社名", "備考2"])
    with pytest.raises(ContractViolation) as err:
        canonicalize_columns(frame, load_aliases())
    assert err.value.details[0]["column_name"] == ["備考2"]


def test_alias_file_is_valid_yaml(tmp_path: Path) -> None:
    custom = tmp_path / "a.yml"
    custom.write_text("company_name: ['社名']\n", encoding="utf-8")
    assert load_aliases(custom)["社名"] == "company_name"
