"""Column-name aliases for product E (see ``aliases.yml``)."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pandas as pd
import yaml

from .validate import ContractViolation

ALIASES_PATH = Path(__file__).with_name("aliases.yml")


def load_aliases(path: Path = ALIASES_PATH) -> dict[str, str]:
    """Return ``{spelling: canonical name}`` from the YAML alias table."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    lookup: dict[str, str] = {}
    for canonical, spellings in raw.items():
        lookup[canonical] = canonical
        for spelling in spellings:
            lookup[_norm(spelling)] = canonical
    return lookup


def _norm(header: str) -> str:
    return unicodedata.normalize("NFKC", header).strip()


def canonicalize_columns(frame: pd.DataFrame, lookup: dict[str, str]) -> pd.DataFrame:
    """Rename the columns of ``frame`` to canonical names.

    A header that is not in the alias table is *not* ignored: the file is rejected with a
    :class:`ContractViolation` that names the unknown headers (header names are not data).
    """
    unknown = [c for c in frame.columns if _norm(str(c)) not in lookup]
    if unknown:
        raise ContractViolation(
            [
                {
                    "column": "<header>",
                    "check": "unknown_column_name",
                    "rows": 0,
                    "column_name": sorted(str(c) for c in unknown),
                }
            ]
        )
    return frame.rename(columns={c: lookup[_norm(str(c))] for c in frame.columns})
