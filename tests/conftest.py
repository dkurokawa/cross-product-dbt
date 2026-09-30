from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from cross_product_platform.config import GeneratorConfig
from cross_product_platform.identity import SALT_ENV
from cross_product_platform.ingest import IngestResult, run_ingest

TEST_SALT = "unit-test-salt-not-a-secret"


def small_config(**overrides: object) -> GeneratorConfig:
    """A tiny world that still crosses the FY2025 -> FY2026 switch (2026-04-01)."""
    base: dict[str, object] = {
        "n_persons": 400,
        "start": date(2026, 2, 1),
        "months": 4,
        "b_snapshot_every_days": 30,
        "n_accounts": 20,
        "gap_day": date(2026, 3, 10),
    }
    base.update(overrides)
    return GeneratorConfig(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SALT_ENV, TEST_SALT)


@pytest.fixture(scope="session")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Iterator[IngestResult]:
    """One landed lake shared by the whole session (generation is the slow part)."""
    root = tmp_path_factory.mktemp("lake")
    previous = os.environ.get(SALT_ENV)
    os.environ[SALT_ENV] = TEST_SALT
    try:
        yield run_ingest(small_config(), root)
    finally:
        if previous is None:
            os.environ.pop(SALT_ENV, None)
        else:
            os.environ[SALT_ENV] = previous


@pytest.fixture
def lake_root(lake: IngestResult) -> Path:
    return lake.root
