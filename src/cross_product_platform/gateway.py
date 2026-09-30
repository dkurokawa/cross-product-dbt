"""The query gateway: the only way a role reads data (design decisions F6, F7).

DuckDB has no permission system, so protection is layered, and each layer works even if the
others are bypassed:

1. **SQL check (sqlglot)** - exactly one SELECT; no table functions, no file/extension/settings
   functions; only tables in the role's allowlist; a hard row limit.
2. **Database settings** - the role's database file is opened ``read_only`` with
   ``enable_external_access = false`` and ``lock_configuration = true``, so file reads, ATTACH,
   INSTALL, LOAD, COPY and changing those settings are refused by DuckDB itself.
3. **Access-layer tables** - the role's file was built from the role's access tables only, so a
   table the role may not see (or a column the role may not see) is not in the file at all.

Every call emits exactly one audit event (``QueryExecuted`` / ``QueryDenied``, see audit.py). If
the event cannot be written the call fails and returns nothing (fail closed).

**The principal is self-declared.** This local template has no authentication: whoever calls the
gateway states who they are, and the audit log records that statement. On BigQuery the IAM
principal recorded in ``INFORMATION_SCHEMA.JOBS`` is the trustworthy counterpart.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import duckdb
from sqlglot import exp, parse
from sqlglot.errors import OptimizeError, ParseError, SqlglotError, TokenError
from sqlglot.optimizer.qualify import qualify

from .audit import QueryDenied, QueryExecuted, now, write_event
from .roles import load_allowlist, role_db_path

MAX_LIMIT: Final = 1000
DIALECT: Final = "duckdb"

#: function names (or prefixes) that touch files, settings, extensions or other queries
FORBIDDEN_FUNCTION_PREFIXES: Final = (
    "read_", "parquet_", "duckdb_", "pragma_", "iceberg_", "delta_", "sniff_", "glob", "query",
    "current_setting", "getenv", "getvariable", "list_files", "load_", "install_", "http_",
)  # fmt: skip
FORBIDDEN_NODES: Final = (
    exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.Copy, exp.Merge, exp.Pragma, exp.Set, exp.Use, exp.Attach, exp.Transaction,
    exp.Commit, exp.Rollback, exp.Into, exp.Lateral, exp.Unnest,
)  # fmt: skip


class GatewayError(RuntimeError):
    """The gateway is misconfigured (unknown role, missing database file)."""


class QueryDeniedError(Exception):
    """A query was refused. ``reason`` never contains data values."""

    def __init__(self, reason: str, analysis: Analysis | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.analysis = analysis


@dataclass(frozen=True)
class Analysis:
    """What the SQL check learned about a statement."""

    normalized: str
    sql_hash: str
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    sensitive: list[str] = field(default_factory=list)
    executable: str = ""


@dataclass(frozen=True)
class QueryResult:
    """Rows returned to the caller."""

    columns: list[str]
    rows: list[tuple[Any, ...]]

    @property
    def row_count(self) -> int:
        """Number of rows returned."""
        return len(self.rows)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(tree: exp.Expr) -> str:
    """The statement with every literal replaced by ``?`` (a literal may be personal data)."""

    def blank(node: exp.Expr) -> exp.Expr:
        return exp.Placeholder() if isinstance(node, exp.Literal) else node

    return str(tree.copy().transform(blank).sql(dialect=DIALECT, normalize=True))


def _function_name(fn: exp.Func) -> str:
    return (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()


def _star_is_plain(star: exp.Star) -> bool:
    """A ``*`` may only be a projection (``*``, ``t.*``) or the argument of count()."""
    parent = star.parent
    if isinstance(parent, exp.Select | exp.Count):
        return True
    return isinstance(parent, exp.Column) and isinstance(parent.parent, exp.Select)


def role_schema(db_path: Path) -> dict[str, list[str]]:
    """The tables and columns that are really in a role's database file."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = 'main' order by table_name, ordinal_position"
        ).fetchall()
    finally:
        con.close()
    schema: dict[str, list[str]] = {}
    for table, column in rows:
        schema.setdefault(str(table), []).append(str(column))
    return schema


def analyze(
    sql: str,
    tables: dict[str, list[str]],
    sensitive_names: set[str],
    limit: int = MAX_LIMIT,
) -> Analysis:
    """Check one statement against a role's tables; raise :class:`QueryDeniedError` if refused.

    ``tables`` maps each allowed table to its columns. The returned analysis has the
    normalized text, its hash, the referenced tables and columns, and the SQL to execute
    (wrapped so that no more than ``limit`` rows come back).
    """
    try:
        statements = parse(sql, read=DIALECT)
    except (ParseError, TokenError):
        raise QueryDeniedError(
            "the statement could not be parsed", Analysis("<unparseable>", _hash(sql))
        ) from None
    if len(statements) != 1 or statements[0] is None:
        raise QueryDeniedError(
            "exactly one statement is allowed", Analysis("<not a single statement>", _hash(sql))
        )
    tree = statements[0]
    normalized = _normalize(tree)
    base = Analysis(normalized, _hash(normalized))
    raw_columns = sorted({c.name for c in tree.find_all(exp.Column)})
    raw_sensitive = sorted(set(raw_columns) & sensitive_names)
    partial = Analysis(normalized, base.sql_hash, [], raw_columns, raw_sensitive)

    def deny(reason: str, tables_seen: list[str] | None = None) -> QueryDeniedError:
        return QueryDeniedError(
            reason,
            Analysis(normalized, base.sql_hash, tables_seen or [], raw_columns, raw_sensitive),
        )

    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise QueryDeniedError(f"only SELECT is allowed (got {type(tree).__name__})", partial)
    if not 1 <= limit <= MAX_LIMIT:
        raise deny(f"limit must be between 1 and {MAX_LIMIT}")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise deny(f"{type(node).__name__} is not allowed")
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise deny(f"function {name} is not allowed")
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
            raise deny("table functions are not allowed")
        if isinstance(node, exp.Columns | exp.PositionalColumn):
            raise deny("dynamic column selection (COLUMNS, #n) is not allowed")
        if isinstance(node, exp.Placeholder | exp.Parameter):
            raise deny("parameters are not allowed")
        if isinstance(node, exp.Star) and not _star_is_plain(node):
            raise deny("* is only allowed as a projection (or inside count)")

    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    seen: list[str] = []
    for table in tree.find_all(exp.Table):
        if table.name in ctes and not table.db:
            continue
        if table.db or table.catalog:
            raise deny("qualified table names are not allowed", seen)
        seen.append(table.name)
        if table.name not in tables:
            raise deny(f"table {table.name} is not available to this role", sorted(set(seen)))
    seen = sorted(set(seen))

    schema: dict[str, object] = {t: dict.fromkeys(cols, "TEXT") for t, cols in tables.items()}
    try:
        qualified = qualify(
            tree.copy(), schema=schema, dialect=DIALECT, validate_qualify_columns=True
        )
    except (OptimizeError, SqlglotError) as err:
        raise deny(f"column check failed: {err}", seen) from None
    if any(
        isinstance(star.parent, exp.Select | exp.Column) for star in qualified.find_all(exp.Star)
    ):
        raise deny("a * could not be expanded against the role's tables", seen)
    aliases = {t.alias_or_name: t.name for t in qualified.find_all(exp.Table)}
    columns = sorted(
        {
            f"{aliases[c.table]}.{c.name}"
            for c in qualified.find_all(exp.Column)
            if c.table in aliases and aliases[c.table] in tables
        }
    )
    sensitive = sorted({c.split(".")[-1] for c in columns} & sensitive_names)
    inner = str(tree.sql(dialect=DIALECT))
    return Analysis(
        normalized,
        base.sql_hash,
        seen,
        columns,
        sensitive,
        f"select * from ({inner}) as q limit {limit}",
    )


def execute_readonly(db_path: Path, sql: str) -> QueryResult:
    """Run ``sql`` on a role database with every DuckDB protection on (layer 2).

    This is the execution layer on its own: it does no SQL checking, so tests can call it
    directly to show that the database settings and the file contents hold by themselves.
    """
    con = duckdb.connect(
        str(db_path),
        read_only=True,
        config={"enable_external_access": False, "lock_configuration": True},
    )
    try:
        cursor = con.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        return QueryResult(columns, [tuple(row) for row in cursor.fetchall()])
    finally:
        con.close()


class Gateway:
    """One role's entry point to the data.

    The role comes from the caller's environment (never from a query), the principal is
    self-declared, and every call is audited.
    """

    def __init__(
        self,
        role: str,
        principal: str,
        *,
        allowlist_path: Path,
        warehouse_dir: Path,
        audit_root: Path,
    ) -> None:
        allowlist = load_allowlist(allowlist_path)
        if role not in allowlist["roles"]:
            raise GatewayError(f"unknown role {role!r}")
        if not principal.strip():
            raise GatewayError("a principal must be declared")
        self.role = role
        self.principal = principal
        self.sensitive_names = set(allowlist.get("sensitive_columns", []))
        self.db_path = role_db_path(warehouse_dir, role)
        if not self.db_path.exists():
            raise GatewayError(f"database file for role {role} not found; run `make roles`")
        # The columns come from the role's database file itself (what is really there), so
        # `*`, `t.*`, EXCLUDE and REPLACE are expanded against the truth, not against a list.
        actual = role_schema(self.db_path)
        granted = allowlist["roles"][role]["tables"]
        missing = sorted(set(granted) - set(actual))
        if missing:
            raise GatewayError(
                f"role database lacks allowlisted tables {missing}; run `make roles`"
            )
        self.tables: dict[str, list[str]] = {name: actual[name] for name in granted}
        self.audit_root = audit_root

    def query(self, sql: str, limit: int = MAX_LIMIT) -> QueryResult:
        """Run one SELECT for this role; audit it; raise :class:`QueryDeniedError` if refused."""
        analysis: Analysis | None = None
        try:
            analysis = analyze(sql, self.tables, self.sensitive_names, limit)
            result = execute_readonly(self.db_path, analysis.executable)
        except QueryDeniedError as err:
            self._audit_denied(err.reason, err.analysis or Analysis("<unknown>", _hash(sql)))
            raise
        except duckdb.Error as err:
            reason = f"the database refused the query ({type(err).__name__})"
            self._audit_denied(reason, analysis or Analysis("<unknown>", _hash(sql)))
            raise QueryDeniedError(reason, analysis) from None
        write_event(
            self.audit_root,
            QueryExecuted(
                occurred_at=now(),
                principal=self.principal,
                role=self.role,
                sql_normalized=analysis.normalized,
                sql_hash=analysis.sql_hash,
                referenced_tables=analysis.tables,
                referenced_columns=analysis.columns,
                sensitive_columns=analysis.sensitive,
                touched_sensitive=bool(analysis.sensitive),
                row_count=result.row_count,
            ),
        )
        return result

    def _audit_denied(self, reason: str, analysis: Analysis) -> None:
        write_event(
            self.audit_root,
            QueryDenied(
                occurred_at=now(),
                principal=self.principal,
                role=self.role,
                sql_normalized=analysis.normalized,
                sql_hash=analysis.sql_hash,
                referenced_tables=analysis.tables,
                referenced_columns=analysis.columns,
                sensitive_columns=analysis.sensitive,
                touched_sensitive=bool(analysis.sensitive),
                deny_reason=reason,
            ),
        )
