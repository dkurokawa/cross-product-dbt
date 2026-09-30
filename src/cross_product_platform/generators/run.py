"""Run all five generators into an ``incoming`` tree."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..config import GeneratorConfig
from .source_a import generate_a
from .source_b import generate_b
from .source_c import generate_c
from .source_d import generate_d
from .source_e import generate_e
from .world import World, build_world


def generate_all(cfg: GeneratorConfig, incoming: Path) -> World:
    """Generate every source under ``incoming/<source>/`` (deterministic in ``cfg.seed``)."""
    world = build_world(cfg)
    kpis: list[pd.DataFrame] = []
    generators = (
        ("A", generate_a),
        ("B", generate_b),
        ("C", generate_c),
        ("D", generate_d),
        ("E", generate_e),
    )
    for index, (name, fn) in enumerate(generators):
        rng = np.random.default_rng([cfg.seed, index + 1])
        kpis.append(fn(world, rng, incoming / name))
    for frame in kpis:
        product = str(frame["product"].iloc[0])
        target = incoming / "reported_kpis" / product
        target.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target / "kpis.parquet", index=False)
    return world
