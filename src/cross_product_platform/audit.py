"""Audit events of the query gateway, written with eventlake (design decision F7).

Every gateway call produces exactly one event: ``QueryExecuted`` or ``QueryDenied``. The dbt
model ``audit_access_log`` reads them back, so "who read a sensitive column" is a SQL query.

The ``principal`` is whatever the caller declared: the local template has no authentication
(see gateway.py). On BigQuery the IAM principal in ``INFORMATION_SCHEMA.JOBS`` is the check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from eventlake import Event, Writer


class QueryExecuted(Event):
    """A query passed every check and returned rows."""

    event_type: ClassVar[str] = "query_executed"

    principal: str
    role: str
    #: SQL with literals replaced by ``?`` (a literal may itself be personal data)
    sql_normalized: str
    sql_hash: str
    referenced_tables: list[str]
    referenced_columns: list[str]
    #: columns declared sensitive that the query mentions
    sensitive_columns: list[str]
    touched_sensitive: bool
    row_count: int


class QueryDenied(Event):
    """A query was refused (by the SQL checks or by the database)."""

    event_type: ClassVar[str] = "query_denied"

    principal: str
    role: str
    sql_normalized: str
    sql_hash: str
    referenced_tables: list[str]
    referenced_columns: list[str]
    sensitive_columns: list[str]
    touched_sensitive: bool
    deny_reason: str


def write_event(audit_root: Path, event: QueryExecuted | QueryDenied) -> None:
    """Append one event to the audit lake (raises if it cannot be written)."""
    with Writer(audit_root) as writer:
        writer.write(event)


def now() -> datetime:
    """The event time."""
    return datetime.now(UTC)
