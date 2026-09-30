"""Landing: contract check, key attachment, quarantine.

Every file delivered to ``<incoming>/`` is read, checked against its pandera contract,
enriched with the columns only Python may compute (design decision F4: ``member_key`` and
friends, so the salt never reaches SQL) and written to ``<root>/<source>/...`` in the same
layout. A file that cannot be read or breaks its contract is *moved* to
``<root>/quarantine/<source>/<date>/`` with a JSON file next to it stating why; rows are
never dropped silently.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import ContractViolation, get_contract, validate_frame
from .contracts.aliases import canonicalize_columns, load_aliases
from .household import household_key
from .identity import Identity, account_key, resolve_identity

_PARTITION = re.compile(r"(?:dt|month)=([0-9]{4}-[0-9]{2}(?:-[0-9]{2})?)")

Enricher = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass(frozen=True)
class QuarantinedFile:
    """One quarantined input file (no data values, only where and why)."""

    source: str
    dataset: str
    file: str
    partition_date: str
    stage: str
    n_violations: int
    reason: str


@dataclass
class LandingReport:
    """What the landing step did."""

    files_landed: Counter[str] = field(default_factory=Counter)
    rows_landed: Counter[str] = field(default_factory=Counter)
    quarantined: list[QuarantinedFile] = field(default_factory=list)
    #: (source, key_method) -> distinct source member ids
    identity: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    #: (source, dataset, partition) -> [files, rows] that were landed
    landed_log: dict[tuple[str, str, str], list[int]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Dataset:
    source: str
    dataset: str
    #: glob relative to the incoming root
    pattern: str
    kind: str  # "parquet" | "csv_sjis" | "csv_utf8"


DATASETS: tuple[_Dataset, ...] = (
    *(
        _Dataset("A", name, f"A/{name}/dt=*/*.parquet", "parquet")
        for name in ("member_registered", "app_opened", "workout_completed", "purchase_made")
    ),
    _Dataset("B", "customers", "B/customers/dt=*/*.parquet", "parquet"),
    _Dataset("B", "bookings", "B/bookings/dt=*/*.parquet", "parquet"),
    *(
        _Dataset("C", name, f"C/{name}/dt=*/*.parquet", "parquet")
        for name in ("members", "contracts_fy2025", "contracts_fy2026", "visits")
    ),
    _Dataset("D", "invoices", "D/invoices/dt=*/*.csv", "csv_sjis"),
    _Dataset("D", "refunds", "D/refunds/dt=*/*.csv", "csv_sjis"),
    _Dataset("E", "accounts", "E/accounts/month=*/*.csv", "csv_utf8"),
    _Dataset("KPI", "reported_kpis", "reported_kpis/*/kpis.parquet", "parquet"),
)


class _KeyCache:
    """Memoizes identity resolution: snapshots repeat the same person many times."""

    def __init__(self, salt: bytes) -> None:
        self._salt = salt
        self._identity: dict[tuple[str, str | None, str | None, str | None], Identity] = {}
        self._household: dict[tuple[str, str | None], str] = {}

    def identity(
        self, source: str, member_id: str, email: str | None, phone: str | None, kana: str | None
    ) -> Identity:
        key = (source, email, phone, kana)
        cached = self._identity.get(key)
        if cached is None:
            cached = resolve_identity(
                self._salt,
                source=source,
                source_member_id=member_id,
                email=email,
                phone=phone,
                kana=kana,
            )
            if cached.key_method != "source_local":
                self._identity[key] = cached
        return cached

    def household(self, own_key: str, guardian_email: str | None) -> str:
        cached = self._household.get((own_key, guardian_email))
        if cached is None:
            cached = household_key(
                self._salt, own_member_key=own_key, guardian_email=guardian_email
            )
            self._household[own_key, guardian_email] = cached
        return cached


def _none(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if value is pd.NA:
        return None
    text = str(value)
    return text if text.strip() else None


class Landing:
    """Lands one incoming tree into a lake root."""

    def __init__(
        self,
        incoming: Path,
        root: Path,
        salt: bytes,
        now: datetime | None = None,
        period: tuple[date, date] | None = None,
    ) -> None:
        self.incoming = incoming
        self.root = root
        self.now = now or datetime.now(UTC)
        #: the first and last day the data covers (from the generator config)
        self.period = period
        self.run_id = f"{self.now:%Y%m%dT%H%M%S%f}Z-{uuid.uuid4().hex[:6]}"
        self.report = LandingReport()
        self._keys = _KeyCache(salt)
        self._aliases = load_aliases()
        self._seen_members: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------ driver
    def run(self) -> LandingReport:
        """Land every dataset and write the ``_meta`` tables."""
        schemas = self.incoming / "A" / "_schemas"
        if schemas.is_dir():
            (self.root / "A").mkdir(parents=True, exist_ok=True)
            shutil.move(str(schemas), str(self.root / "A" / "_schemas"))
        for ds in DATASETS:
            for path in sorted(self.incoming.glob(ds.pattern)):
                self._land_file(ds, path)
        self._write_meta()
        self._remove_empty_dirs(self.incoming)
        return self.report

    def _land_file(self, ds: _Dataset, path: Path) -> None:
        rel = path.relative_to(self.incoming)
        stage = "read"
        try:
            frame, schema = self._read(ds, path)
            stage = "contract"
            if ds.source == "E":
                frame = canonicalize_columns(frame, self._aliases)
            frame = validate_frame(get_contract(ds.source, ds.dataset), frame)
            frame = self._enrich(ds, frame)
        except ContractViolation as err:
            self._quarantine(ds, path, stage, err.details)
            return
        except (OSError, ValueError, UnicodeDecodeError, pa.ArrowException) as err:
            self._quarantine(
                ds, path, stage, [{"column": "<file>", "check": type(err).__name__, "rows": 0}]
            )
            return
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write(ds, frame, schema, target)
        path.unlink()
        self.report.files_landed[ds.source] += 1
        self.report.rows_landed[ds.source] += len(frame)
        entry = self.report.landed_log.setdefault(
            (ds.source, ds.dataset, self._partition(rel)), [0, 0]
        )
        entry[0] += 1
        entry[1] += len(frame)

    # ------------------------------------------------------------------ IO
    def _read(self, ds: _Dataset, path: Path) -> tuple[pd.DataFrame, pa.Schema | None]:
        if ds.kind == "parquet":
            table = pq.read_table(path)
            return table.to_pandas(), table.schema
        encoding = "cp932" if ds.kind == "csv_sjis" else "utf-8-sig"
        # Shift_JIS is decoded here, not in DuckDB: DuckDB needs its `encodings` extension
        # (downloaded on first use) for it, and landing must work offline.
        return pd.read_csv(path, encoding=encoding, dtype=str, keep_default_na=False), None

    def _write(
        self, ds: _Dataset, frame: pd.DataFrame, schema: pa.Schema | None, target: Path
    ) -> None:
        if ds.kind == "parquet":
            if schema is not None and ds.source == "A":
                table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
                pq.write_table(table, target)
            else:
                frame.to_parquet(target, index=False)
        else:
            frame.to_csv(target, index=False, encoding="utf-8")

    def _partition(self, rel: Path) -> str:
        """The ``dt=`` / ``month=`` value of a path, or the ingest day if it has none."""
        match = _PARTITION.search(str(rel))
        return match.group(1) if match else self.now.date().isoformat()

    def _quarantine(
        self, ds: _Dataset, path: Path, stage: str, details: list[dict[str, object]]
    ) -> None:
        rel = path.relative_to(self.incoming)
        partition = self._partition(rel)
        target_dir = self.root / "quarantine" / ds.source / partition
        target_dir.mkdir(parents=True, exist_ok=True)
        name = f"{ds.dataset}__{path.name}"
        shutil.move(str(path), target_dir / name)
        first = details[0] if details else {}
        reason = f"{first.get('check', 'unknown')} on {first.get('column', '?')}"
        (target_dir / f"{name}.reason.json").write_text(
            json.dumps(
                {
                    "source": ds.source,
                    "dataset": ds.dataset,
                    "file": str(rel),
                    "stage": stage,
                    "quarantined_at": self.now.isoformat(),
                    "violations": details,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.report.quarantined.append(
            QuarantinedFile(ds.source, ds.dataset, str(rel), partition, stage, len(details), reason)
        )

    # ------------------------------------------------------------------ enrichment
    def _enrich(self, ds: _Dataset, frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.copy()
        ingested = pd.Timestamp(self.now).tz_convert("UTC").as_unit("us")
        if ds.source == "E":
            frame["account_key"] = [account_key(_none(v)) for v in frame["company_name"]]
        elif (ds.source, ds.dataset) == ("A", "member_registered"):
            frame = self._keys_for(
                frame, "A", frame["member_id"], frame["email"], frame["phone"], frame["name_kana"],
                frame["guardian_email"],
            )  # fmt: skip
        elif (ds.source, ds.dataset) == ("B", "customers"):
            ids = frame["tenant_id"] + "/" + frame["member_no"].astype(str)
            frame = self._keys_for(
                frame, "B", ids, frame["email"], frame["phone"], frame["name_kana"], None
            )
        elif (ds.source, ds.dataset) == ("C", "members"):
            frame = self._keys_for(
                frame, "C", frame["member_id"].astype(str), frame["email"], frame["phone"],
                frame["name_kana"], frame["guardian_email"],
            )  # fmt: skip
        elif (ds.source, ds.dataset) == ("D", "invoices"):
            frame = self._keys_for(
                frame,
                "D",
                frame["請求番号"],
                None,
                frame["電話番号"],
                frame["氏名カナ"],
                None,
                count_by_key=True,
            )
        frame["ingested_at"] = ingested
        frame["ingested_at"] = frame["ingested_at"].astype("datetime64[us, UTC]")
        return frame

    def _keys_for(
        self,
        frame: pd.DataFrame,
        source: str,
        ids: pd.Series,
        emails: pd.Series | None,
        phones: pd.Series | None,
        kanas: pd.Series | None,
        guardian_emails: pd.Series | None,
        count_by_key: bool = False,
    ) -> pd.DataFrame:
        n = len(frame)
        member_key: list[str] = []
        link_key: list[str | None] = []
        method: list[str] = []
        household: list[str] = []
        seen = self._seen_members.setdefault(source, {})
        for i in range(n):
            ident = self._keys.identity(
                source,
                str(ids.iloc[i]),
                _none(emails.iloc[i]) if emails is not None else None,
                _none(phones.iloc[i]) if phones is not None else None,
                _none(kanas.iloc[i]) if kanas is not None else None,
            )
            guardian = _none(guardian_emails.iloc[i]) if guardian_emails is not None else None
            member_key.append(ident.member_key)
            link_key.append(ident.link_key)
            method.append(ident.key_method)
            household.append(self._keys.household(ident.member_key, guardian))
            # D has no member id: its people are counted by their derived key instead.
            seen[ident.member_key if count_by_key else str(ids.iloc[i])] = ident.key_method
        frame["member_key"] = pd.Series(member_key, index=frame.index, dtype="object")
        frame["link_key"] = pd.Series(link_key, index=frame.index, dtype="object")
        frame["key_method"] = pd.Series(method, index=frame.index, dtype="object")
        frame["household_key"] = pd.Series(household, index=frame.index, dtype="object")
        return frame

    # ------------------------------------------------------------------ meta tables
    def _write_meta(self) -> None:
        stats = pd.DataFrame(
            [
                {"source": source, "key_method": method, "n_members": len(ids)}
                for source, seen in sorted(self._seen_members.items())
                for method, ids in _group_by_method(seen)
            ],
            columns=["source", "key_method", "n_members"],
        ).astype({"source": "object", "key_method": "object", "n_members": "int64"})
        quarantine = pd.DataFrame(
            [q.__dict__ | {"quarantined_at": self.now} for q in self.report.quarantined],
            columns=[
                "source", "dataset", "file", "partition_date", "stage", "n_violations", "reason",
                "quarantined_at",
            ],
        )  # fmt: skip
        quarantine = quarantine.astype(
            {
                "source": "object",
                "dataset": "object",
                "file": "object",
                "partition_date": "object",
                "stage": "object",
                "n_violations": "int64",
                "reason": "object",
            }
        )  # fmt: skip
        quarantine["quarantined_at"] = pd.Series(
            [self.now] * len(quarantine), dtype="datetime64[us, UTC]"
        )
        landing = pd.DataFrame(
            [
                {
                    "source": k[0],
                    "dataset": k[1],
                    "partition_date": k[2],
                    "files_landed": v[0],
                    "rows_landed": v[1],
                }
                for k, v in sorted(self.report.landed_log.items())
            ],
            columns=["source", "dataset", "partition_date", "files_landed", "rows_landed"],
        ).astype(
            {
                "source": "object",
                "dataset": "object",
                "partition_date": "object",
                "files_landed": "int64",
                "rows_landed": "int64",
            }
        )
        tables = [
            ("identity_stats", stats),
            ("quarantine_log", quarantine),
            ("landing_log", landing),
        ]
        if self.period is not None:
            period = pd.DataFrame(
                {"period_start": [self.period[0]], "period_end": [self.period[1]]}
            )
            tables.append(("period", period))
        for name, frame in tables:
            # One file per run: an earlier run's history is never overwritten (a later
            # `--no-generate` run adds to it). Downstream models read every file and can tell
            # the runs apart by run_id.
            frame = frame.copy()
            frame.insert(0, "run_id", self.run_id)
            target = self.root / "_meta" / name
            target.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(target / f"run-{self.run_id}.parquet", index=False)

    def _remove_empty_dirs(self, top: Path) -> None:
        for directory in sorted((p for p in top.rglob("*") if p.is_dir()), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        if top.is_dir() and not any(top.iterdir()):
            top.rmdir()


def _group_by_method(seen: dict[str, str]) -> list[tuple[str, set[str]]]:
    grouped: dict[str, set[str]] = {}
    for member_id, method in seen.items():
        grouped.setdefault(method, set()).add(member_id)
    return sorted(grouped.items())
