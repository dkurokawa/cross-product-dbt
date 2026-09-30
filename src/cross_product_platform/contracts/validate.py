"""Run a contract over a frame and summarize violations without echoing any data value."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from pandera.errors import SchemaErrors

from .registry import Contract


@dataclass(frozen=True)
class ContractViolation(Exception):  # noqa: N818 - a value object first, an exception second
    """A contract failed. ``details`` never contains cell values, only names and counts."""

    details: list[dict[str, object]]

    def __str__(self) -> str:
        return f"{len(self.details)} contract violation(s)"


def validate_frame(contract: Contract, frame: pd.DataFrame) -> pd.DataFrame:
    """Return the validated (coerced) frame or raise :class:`ContractViolation`."""
    try:
        return contract.schema.validate(frame, lazy=True)
    except SchemaErrors as err:
        raise ContractViolation(_summarize(err.failure_cases)) from None


def _summarize(cases: pd.DataFrame) -> list[dict[str, object]]:
    """One entry per (column, check); structural failures name the offending column.

    Row-level failures report only how many rows failed - never the values, which
    may be personal data.
    """
    structural = {"column_in_dataframe", "column_in_schema", "dataframe_column_names"}
    out: list[dict[str, object]] = []
    for (column, check), group in cases.groupby(
        [cases["column"].astype(str), cases["check"].astype(str)], sort=True, dropna=False
    ):
        entry: dict[str, object] = {"column": column, "check": check, "rows": int(len(group))}
        if check in structural:
            names = sorted({str(v) for v in group["failure_case"]})
            entry["column_name"] = names
            entry["column"] = ", ".join(names)
        out.append(entry)
    return out
