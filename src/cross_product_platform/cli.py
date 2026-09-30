"""Command line entry point (``platform``)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from . import __version__
from .access import (
    PolicyError,
    check_access_files,
    check_access_manifest,
    generate_access_files,
    load_columns,
    load_policy,
    write_access_files,
)
from .config import GeneratorConfig
from .dialect_lint import scan_directory
from .identity import MissingSaltError
from .ingest import LakeNotEmptyError, run_ingest
from .metrics.check import (
    check_generated,
    check_manifest,
    check_reported,
    load_json,
    write_files,
)
from .metrics.codegen import generate_files
from .metrics.spec import SpecError, load_spec
from .scan import check_model_declarations, load_source_declarations, scan_lake


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

    metrics = sub.add_parser("metrics", help="generate / check the metric models from metrics.yml")
    msub = metrics.add_subparsers(dest="metrics_command", required=True)
    for name, text in (("generate", "write the generated files"), ("check", "run the guards")):
        p = msub.add_parser(name, help=text)
        p.add_argument("--spec", type=Path, default=Path("metrics/metrics.yml"))
        p.add_argument("--repo-root", type=Path, default=Path("."))
        if name == "check":
            p.add_argument("--manifest", type=Path, default=Path("dbt/target/manifest.json"))
            p.add_argument("--catalog", type=Path, default=Path("dbt/target/catalog.json"))
            p.add_argument("--root", type=Path, default=None, help="lake root (for check 3)")
            p.add_argument("--only-generated", action="store_true", help="skip checks 2 and 3")

    scan = sub.add_parser("scan", help="find personal data that is undeclared or declared 'none'")
    scan.add_argument("--root", type=Path, required=True, help="lake root")
    scan.add_argument("--sources", type=Path, default=Path("dbt/models/bronze/_sources.yml"))
    scan.add_argument("--manifest", type=Path, default=Path("dbt/target/manifest.json"))
    scan.add_argument("--catalog", type=Path, default=Path("dbt/target/catalog.json"))
    scan.add_argument("--skip-models", action="store_true", help="only scan the landed data")

    access = sub.add_parser("access", help="generate / check the access layer from access.yml")
    asub = access.add_subparsers(dest="access_command", required=True)
    for name in ("generate", "check"):
        p = asub.add_parser(name)
        p.add_argument("--policy", type=Path, default=Path("policies/access.yml"))
        p.add_argument("--spec", type=Path, default=Path("metrics/metrics.yml"))
        p.add_argument("--repo-root", type=Path, default=Path("."))
        if name == "check":
            p.add_argument("--manifest", type=Path, default=None, help="also check dbt's manifest")

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


def _metrics(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(args.spec)
    except (SpecError, OSError) as err:
        print(f"metrics: {err}", file=sys.stderr)
        return 2
    if args.metrics_command == "generate":
        files = generate_files(spec)
        write_files(files, args.repo_root)
        print(f"metrics generate: {len(files)} files written")
        return 0
    problems = check_generated(spec, args.repo_root)
    if not args.only_generated:
        try:
            manifest = load_json(args.manifest)
            catalog = load_json(args.catalog) if args.catalog.exists() else None
        except (OSError, ValueError) as err:
            print(f"metrics check: cannot read dbt artifacts: {err}", file=sys.stderr)
            return 2
        problems += check_manifest(spec, manifest, catalog)
        if args.root is not None:
            problems += check_reported(spec, args.root)
    for problem in problems:
        print(f"metrics check: {problem}", file=sys.stderr)
    print(f"metrics check: {len(problems)} problem(s)")
    return 1 if problems else 0


def _access(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.policy)
        files = generate_access_files(policy, load_columns(args.repo_root, load_spec(args.spec)))
    except (PolicyError, SpecError, OSError) as err:
        print(f"access: {err}", file=sys.stderr)
        return 2
    if args.access_command == "generate":
        write_access_files(files, args.repo_root)
        print(f"access generate: {len(files)} files written")
        return 0
    problems = check_access_files(files, args.repo_root)
    if args.manifest is not None:
        problems += check_access_manifest(policy, load_json(args.manifest))
    for problem in problems:
        print(f"access check: {problem}", file=sys.stderr)
    print(f"access check: {len(problems)} problem(s)")
    return 1 if problems else 0


def _scan(args: argparse.Namespace) -> int:
    problems, warnings = scan_lake(args.root, load_source_declarations(args.sources))
    if not args.skip_models:
        try:
            manifest = load_json(args.manifest)
            catalog = load_json(args.catalog) if args.catalog.exists() else None
        except (OSError, ValueError) as err:
            print(f"scan: cannot read dbt artifacts: {err}", file=sys.stderr)
            return 2
        problems += check_model_declarations(manifest, catalog)
    for warning in warnings:
        print(f"scan: warning: {warning}")
    for problem in problems:
        print(f"scan: {problem}", file=sys.stderr)
    print(f"scan: {len(problems)} problem(s), {len(warnings)} warning(s)")
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "ingest":
        return _ingest(args)
    if args.command == "access":
        return _access(args)
    if args.command == "scan":
        return _scan(args)
    if args.command == "metrics":
        return _metrics(args)
    if args.command == "lint-dialect":
        violations = scan_directory(args.models_dir)
        for violation in violations:
            print(violation, file=sys.stderr)
        print(f"dialect lint: {len(violations)} violation(s)")
        return 1 if violations else 0
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
