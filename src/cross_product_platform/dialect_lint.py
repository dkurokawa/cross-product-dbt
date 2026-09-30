"""Dialect denylist lint for dbt model SQL (design section 6).

Model bodies must not contain functions that only one warehouse has. Dialect
differences are confined to dbt cross-db macros (``dbt.date_trunc``, ``dbt.dateadd``,
``dbt.safe_cast``...) and to this project's own ``adapter.dispatch`` macros in
``dbt/macros``; the macros directory is deliberately *not* scanned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Function names / operators that belong to exactly one warehouse family.
DENYLIST: dict[str, str] = {
    # DuckDB
    r"\bstrptime\s*\(": "DuckDB strptime()",
    r"\bstrftime\s*\(": "DuckDB strftime()",
    r"\bepoch_ms\s*\(": "DuckDB epoch_ms()",
    r"\barg_(min|max)\s*\(": "DuckDB arg_min()/arg_max()",
    r"\bstruct_pack\s*\(": "DuckDB struct_pack()",
    r"\blist_(value|agg|aggregate|contains)\s*\(": "DuckDB list_* function",
    r"\bregexp_matches\s*\(": "DuckDB regexp_matches()",
    r"\bread_(csv|csv_auto|parquet|json)\s*\(": "DuckDB table function",
    r"\btry_cast\s*\(": "DuckDB/Snowflake try_cast() (use the safe_cast macro)",
    r"(?<!:)::(?!:)": "PostgreSQL/DuckDB '::' cast operator",
    r"\bat\s+time\s+zone\b": "AT TIME ZONE (use the to_utc_from_jst macro)",
    # BigQuery
    r"(?<![.\w])safe_cast\s*\(": "BigQuery SAFE_CAST() (use the safe_cast macro)",
    r"\bparse_(date|timestamp|datetime)\s*\(": "BigQuery parse_date()/parse_timestamp()",
    r"\bformat_(date|timestamp|datetime)\s*\(": "BigQuery format_date()/format_timestamp()",
    r"\btimestamp_(trunc|seconds|millis|add|sub)\s*\(": "BigQuery TIMESTAMP_* function",
    r"\bdate_(sub|add)\s*\(": "BigQuery DATE_SUB()/DATE_ADD()",
    r"\bcountif\s*\(": "BigQuery COUNTIF()",
    r"\bgenerate_date_array\s*\(": "BigQuery GENERATE_DATE_ARRAY()",
    r"\bsafe_divide\s*\(": "BigQuery SAFE_DIVIDE()",
    r"\bstring_agg\s*\(": "BigQuery/Postgres STRING_AGG()",
}

_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in DENYLIST.items()]


@dataclass(frozen=True)
class Violation:
    """One denylisted construct in one line of one file."""

    path: Path
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


def _strip_comments(line: str) -> str:
    """Drop ``-- ...`` comments and quoted strings so prose cannot trip the lint."""
    line = re.sub(r"'[^']*'", "''", line)
    return line.split("--", 1)[0]


def scan_text(path: Path, text: str) -> list[Violation]:
    """Return every denylisted construct in ``text`` (Jinja comments are skipped)."""
    text = re.sub(r"\{#.*?#\}", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL)
    found: list[Violation] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comments(raw)
        for pattern, why in _COMPILED:
            if pattern.search(line):
                found.append(Violation(path, number, why))
    return found


def scan_directory(models_dir: Path) -> list[Violation]:
    """Scan every ``*.sql`` file below ``models_dir``."""
    violations: list[Violation] = []
    for sql_file in sorted(models_dir.rglob("*.sql")):
        violations.extend(scan_text(sql_file, sql_file.read_text(encoding="utf-8")))
    return violations
