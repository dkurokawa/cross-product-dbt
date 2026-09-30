"""``platform ingest``: generate the five sources, then land them with contracts."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import GeneratorConfig
from .generators.run import generate_all
from .identity import get_salt
from .landing import Landing, LandingReport

#: Everything ingest writes below the lake root (removed again by ``--overwrite``).
MANAGED_DIRS = ("A", "B", "C", "D", "E", "reported_kpis", "quarantine", "_meta", "_incoming")


class LakeNotEmptyError(RuntimeError):
    """The target root already holds landed data and ``overwrite`` was not requested."""


@dataclass(frozen=True)
class IngestResult:
    """Summary of one ingest run."""

    report: LandingReport
    root: Path


def _clear_managed(root: Path) -> None:
    for name in MANAGED_DIRS:
        target = root / name
        if target.is_dir():
            shutil.rmtree(target)


def run_ingest(
    cfg: GeneratorConfig, root: Path, *, overwrite: bool = False, generate: bool = True
) -> IngestResult:
    """Generate (optionally) and land everything under ``root``.

    ``root`` must not hold a previous run unless ``overwrite`` is set; only the directories
    this command manages are removed then.
    """
    salt = get_salt()  # fail before doing any work if no salt is configured
    existing = [name for name in MANAGED_DIRS if (root / name).exists() and name != "_incoming"]
    if existing and generate:
        if not overwrite:
            raise LakeNotEmptyError(
                f"{root} already contains {', '.join(existing)}; use --overwrite"
            )
        _clear_managed(root)
    root.mkdir(parents=True, exist_ok=True)
    incoming = root / "_incoming"
    if generate:
        generate_all(cfg, incoming)
    report = Landing(incoming, root, salt, period=(cfg.start, cfg.end)).run()
    return IngestResult(report=report, root=root)
