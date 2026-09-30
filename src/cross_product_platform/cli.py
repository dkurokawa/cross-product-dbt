"""Command line entry point (``platform``)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .dialect_lint import scan_directory


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (exposed for tests)."""
    parser = argparse.ArgumentParser(prog="platform", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    lint = sub.add_parser("lint-dialect", help="grep dbt models for warehouse-specific functions")
    lint.add_argument("models_dir", type=Path, nargs="?", default=Path("dbt/models"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "lint-dialect":
        violations = scan_directory(args.models_dir)
        for violation in violations:
            print(violation, file=sys.stderr)
        print(f"dialect lint: {len(violations)} violation(s)")
        return 1 if violations else 0
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
