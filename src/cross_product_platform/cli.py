"""Command line entry point (``platform``)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from . import __version__
from .config import GeneratorConfig
from .dialect_lint import scan_directory
from .identity import MissingSaltError
from .ingest import LakeNotEmptyError, run_ingest


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (exposed for tests)."""
    parser = argparse.ArgumentParser(prog="platform", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "ingest", help="generate the five synthetic sources and land them with data contracts"
    )
    ingest.add_argument("--root", type=Path, required=True, help="lake root (local path)")
    ingest.add_argument("--seed", type=int, default=42)
    ingest.add_argument("--persons", type=int, default=5000, help="distinct people in the world")
    ingest.add_argument("--months", type=int, default=12)
    ingest.add_argument("--start", type=date.fromisoformat, default=date(2025, 10, 1))
    ingest.add_argument("--overlap", type=float, default=0.3, help="P(person in a 2nd product)")
    ingest.add_argument(
        "--gap-day",
        type=date.fromisoformat,
        default=None,
        help="drop every product-A event of this UTC day (anomaly-test input)",
    )
    ingest.add_argument(
        "--no-bad-files", action="store_true", help="do not inject files to quarantine"
    )
    ingest.add_argument(
        "--unmapped-plan",
        action="store_true",
        help="add a contract with a plan code not in the seed",
    )
    ingest.add_argument("--overwrite", action="store_true", help="replace a previous run in --root")
    ingest.add_argument(
        "--no-generate", action="store_true", help="only land what is already in <root>/_incoming"
    )

    lint = sub.add_parser("lint-dialect", help="grep dbt models for warehouse-specific functions")
    lint.add_argument("models_dir", type=Path, nargs="?", default=Path("dbt/models"))
    return parser


def _ingest(args: argparse.Namespace) -> int:
    cfg = GeneratorConfig(
        seed=args.seed,
        n_persons=args.persons,
        start=args.start,
        months=args.months,
        overlap=args.overlap,
        gap_day=args.gap_day,
        inject_bad_files=not args.no_bad_files,
        inject_unmapped_plan=args.unmapped_plan,
    )
    try:
        result = run_ingest(cfg, args.root, overwrite=args.overwrite, generate=not args.no_generate)
    except (MissingSaltError, LakeNotEmptyError) as err:
        print(f"ingest: {err}", file=sys.stderr)
        return 1
    report = result.report
    for source in sorted(report.files_landed):
        files, rows = report.files_landed[source], report.rows_landed[source]
        print(f"landed {source}: {files} files, {rows} rows")
    for q in report.quarantined:
        print(f"quarantined {q.source}/{q.dataset}: {q.file} ({q.reason})")
    print(
        f"ingest: {sum(report.files_landed.values())} files landed, "
        f"{len(report.quarantined)} quarantined -> {result.root}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "ingest":
        return _ingest(args)
    if args.command == "lint-dialect":
        violations = scan_directory(args.models_dir)
        for violation in violations:
            print(violation, file=sys.stderr)
        print(f"dialect lint: {len(violations)} violation(s)")
        return 1 if violations else 0
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
