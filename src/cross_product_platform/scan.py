"""``platform scan``: find personal data nobody declared (design decision F3).

Columns carry a declared class in dbt (``config.meta.pii`` = none / quasi / direct / sensitive).
This module walks the *landed data*, detects personal data independently of the declarations
(column-name patterns, value patterns, free-text shape) and reports every column that was
detected as personal but is declared ``none`` or not declared at all. It also checks that every
column that reaches silver, gold or access has a valid declaration.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd
import pyarrow.parquet as pq
import yaml

from .landing import DATASETS

PII_CLASSES: Final = ("none", "quasi", "direct", "sensitive")
MASKS: Final = ("email_domain", "phone_last4", "birth_year", "initial")
_RANK = {name: i for i, name in enumerate(PII_CLASSES)}

_NOT_A_PERSON = {"company", "plan", "file", "event", "type", "sku", "facility", "workout", "tenant"}
_HEALTH = re.compile(
    r"health|medical|condition|injur|diagnos|disease|allerg|illness|symptom|持病|病歴|けが"
)
_EMAIL_NAME = re.compile(r"e-?mail|メール")
_PHONE_NAME = re.compile(r"phone|telephone|(^|_)tel($|_)|mobile|電話")
_BIRTH_NAME = re.compile(r"birth|(^|_)dob($|_)|生年月日")
_ADDRESS_NAME = re.compile(r"address|postal|(^|_)zip($|_)|住所|郵便")
_EMAIL_VALUE = re.compile(r"^\s*[\w.+-]+@[\w-]+(\.[\w-]+)+\s*$")
_PHONE_VALUE = re.compile(r"^\+?[\d\-\s()]{10,}$")

SAMPLE_FILES: Final = 3
SAMPLE_ROWS: Final = 500
_SCANNED_SOURCES: Final = {"A", "B", "C", "D", "E"}


@dataclass(frozen=True)
class Detection:
    """What the detectors think a column holds."""

    level: str
    reasons: tuple[str, ...]


def detect(column: str, values: Sequence[Any]) -> Detection:
    """Classify one column from its name and a sample of its values."""
    level = "none"
    reasons: list[str] = []

    def raise_to(new: str, why: str) -> None:
        nonlocal level
        reasons.append(why)
        if _RANK[new] > _RANK[level]:
            level = new

    name = unicodedata.normalize("NFKC", column).lower()
    tokens = set(re.split(r"[^a-z0-9ぁ-んァ-ヶ一-龠]+", name))
    if _EMAIL_NAME.search(name):
        raise_to("direct", "column name looks like an e-mail address")
    if _PHONE_NAME.search(name):
        raise_to("direct", "column name looks like a phone number")
    if _BIRTH_NAME.search(name):
        raise_to("direct", "column name looks like a birth date")
    if _ADDRESS_NAME.search(name):
        raise_to("direct", "column name looks like an address")
    is_person_name = (
        "name" in tokens or "kana" in tokens or "氏名" in name or "カナ" in name
    ) and (not tokens & _NOT_A_PERSON)
    if is_person_name:
        raise_to("direct", "column name looks like a person's name")
    if _HEALTH.search(name):
        raise_to("sensitive", "column name looks like health information")

    text = [
        unicodedata.normalize("NFKC", str(v)) for v in values if isinstance(v, str) and v.strip()
    ]
    if text:
        share = 1 / len(text)
        if sum(bool(_EMAIL_VALUE.match(v)) for v in text) * share >= 0.5:
            raise_to("direct", "values look like e-mail addresses")
        phones = [v for v in text if _PHONE_VALUE.match(v) and len(re.sub(r"\D", "", v)) >= 10]
        if len(phones) * share >= 0.5:
            raise_to("direct", "values look like phone numbers")
        mean_len = sum(len(v) for v in text) / len(text)
        mean_words = sum(len(v.split()) for v in text) / len(text)
        if mean_len >= 25 and mean_words >= 3:
            raise_to("sensitive", "values look like free text (may hold health details)")
    return Detection(level, tuple(reasons))


def load_source_declarations(sources_yml: Path) -> dict[tuple[str, str], dict[str, str]]:
    """``{(source, table): {column: pii class}}`` from the dbt sources file."""
    raw = yaml.safe_load(sources_yml.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict[str, str]] = {}
    for source in raw.get("sources", []):
        for table in source.get("tables", []):
            declared: dict[str, str] = {}
            for column in table.get("columns", []):
                meta = (column.get("config") or {}).get("meta") or column.get("meta") or {}
                if "pii" in meta:
                    declared[str(column["name"])] = str(meta["pii"])
            out[str(source["name"]).upper(), str(table["name"])] = declared
    return out


def _sample(root: Path, pattern: str, kind: str) -> tuple[list[str], dict[str, list[Any]]]:
    """All column names of a dataset (from every file) and a sample of their values."""
    files = sorted(root.glob(pattern))
    names: list[str] = []
    for path in files:
        if kind == "parquet":
            header = pq.read_schema(path).names
        else:
            with path.open(encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle), [])
        names += [n for n in header if n not in names]
    picks = files[:: max(1, len(files) // SAMPLE_FILES)][:SAMPLE_FILES] if files else []
    values: dict[str, list[Any]] = {n: [] for n in names}
    for path in picks:
        if kind == "parquet":
            frame = pq.read_table(path).to_pandas().head(SAMPLE_ROWS)
        else:
            frame = pd.read_csv(path, dtype=str, keep_default_na=False, nrows=SAMPLE_ROWS)
        for column in frame.columns:
            values[str(column)] += frame[column].tolist()
    return names, values


def scan_lake(
    root: Path, declarations: dict[tuple[str, str], dict[str, str]]
) -> tuple[list[str], list[str]]:
    """Scan the landed source data; returns ``(problems, warnings)``."""
    problems: list[str] = []
    warnings: list[str] = []
    for ds in DATASETS:
        if ds.source not in _SCANNED_SOURCES:
            continue
        kind = "parquet" if ds.kind == "parquet" else "csv"
        names, values = _sample(root, ds.pattern, kind)
        declared = declarations.get((ds.source, ds.dataset), {})
        for column in names:
            found = detect(column, values[column])
            given = declared.get(column)
            label = f"{ds.source}.{ds.dataset}.{column}"
            if given is not None and given not in PII_CLASSES:
                problems.append(
                    f"{label}: declared pii class {given!r} is not one of {PII_CLASSES}"
                )
            elif found.level != "none" and given in (None, "none"):
                state = "undeclared" if given is None else "declared none"
                problems.append(
                    f"{label}: detected {found.level} ({'; '.join(found.reasons)}) but {state}"
                )
            elif given is not None and _RANK[given] < _RANK[found.level]:
                warnings.append(f"{label}: declared {given}, detectors suggest {found.level}")
    return problems, warnings


def check_model_declarations(
    manifest: dict[str, Any],
    catalog: dict[str, Any] | None,
    layers: Sequence[str] = ("silver", "gold", "access"),
) -> list[str]:
    """Every column of every model / seed in ``layers`` needs a valid ``meta.pii``.

    A ``direct`` column in silver or gold must also name its ``mask`` (the access layer uses it).
    """
    problems: list[str] = []
    for uid, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") not in {"model", "seed"} or node.get("schema") not in layers:
            continue
        declared = node.get("columns", {})
        names = list(declared)
        if catalog is not None:
            names += [c for c in catalog.get("nodes", {}).get(uid, {}).get("columns", {})]
        for column in dict.fromkeys(names):
            entry: dict[str, Any] = next(
                (d for k, d in declared.items() if k.lower() == column.lower()), {}
            )
            meta = (entry.get("config") or {}).get("meta") or entry.get("meta") or {}
            pii = meta.get("pii")
            label = f"{node.get('name', uid)}.{column}"
            if pii is None:
                problems.append(f"{label}: no meta.pii declaration")
            elif pii not in PII_CLASSES:
                problems.append(f"{label}: invalid meta.pii {pii!r}")
            elif (
                pii == "direct"
                and node.get("schema") in {"silver", "gold"}
                and meta.get("mask") not in MASKS
            ):
                problems.append(f"{label}: direct column needs meta.mask in {MASKS}")
    return problems
