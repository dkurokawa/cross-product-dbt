"""Load and validate ``metrics/metrics.yml`` (design decision F1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

KINDS: Final = (
    "distinct_entities_in_window",
    "sum_by_month",
    "lapsed_between_windows",
    "rows_per_active",
)
DIRECTIONS: Final = ("higher", "lower", "any")
SCOPE_KINDS: Final = ("all", "source", "population")


class SpecError(ValueError):
    """metrics.yml is malformed."""


@dataclass(frozen=True)
class Variant:
    """A product's own KPI that shares a metric's idea under another definition."""

    product: str
    reported_as: str
    definition: str
    expected_direction: str = "any"
    direction_tolerance_pct: float = 0.0
    threshold_pct: float = 0.0
    comparable: bool = True
    note: str = ""


@dataclass(frozen=True)
class Metric:
    """One shared metric with its canonical definition and its known variants."""

    name: str
    description: str
    owner: str
    entity: str
    grain: tuple[str, ...]
    kind: str
    definition: dict[str, Any]
    scopes: dict[str, str]
    known_variants: tuple[Variant, ...]


@dataclass(frozen=True)
class MetricsSpec:
    """The whole file."""

    metrics: tuple[Metric, ...] = field(default_factory=tuple)

    def by_name(self, name: str) -> Metric:
        """The metric called ``name``."""
        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise SpecError(f"unknown metric {name!r}")

    def reported_names(self) -> set[tuple[str, str]]:
        """Every ``(product, reported_as)`` some metric knows as a variant."""
        return {(v.product, v.reported_as) for m in self.metrics for v in m.known_variants}

    def guarded_column_names(self) -> set[str]:
        """Names no non-generated model may use as a column (check 2)."""
        names = {m.name for m in self.metrics}
        names |= {v.reported_as for m in self.metrics for v in m.known_variants}
        return names

    def scopes_of(self, metric: Metric) -> dict[str, str]:
        """The product scopes of ``metric`` (derived metrics inherit from their active metric)."""
        if metric.scopes:
            return metric.scopes
        active = metric.definition.get("active_metric")
        if isinstance(active, str):
            return self.scopes_of(self.by_name(active))
        raise SpecError(f"metric {metric.name} has no scopes")


def _variant(raw: dict[str, Any], metric: str) -> Variant:
    try:
        variant = Variant(
            product=str(raw["product"]),
            reported_as=str(raw["reported_as"]),
            definition=str(raw["definition"]),
            expected_direction=str(raw.get("expected_direction", "any")),
            direction_tolerance_pct=float(raw.get("direction_tolerance_pct", 0)),
            threshold_pct=float(raw.get("threshold_pct", 0)),
            comparable=bool(raw.get("comparable", True)),
            note=str(raw.get("note", "")),
        )
    except KeyError as err:
        raise SpecError(f"{metric}: variant is missing {err}") from None
    if variant.expected_direction not in DIRECTIONS:
        raise SpecError(f"{metric}/{variant.reported_as}: bad expected_direction")
    if variant.comparable and variant.threshold_pct <= 0:
        raise SpecError(f"{metric}/{variant.reported_as}: a comparable variant needs threshold_pct")
    if not variant.comparable and not variant.note:
        raise SpecError(f"{metric}/{variant.reported_as}: a non-comparable variant needs a note")
    return variant


def parse_spec(raw: dict[str, Any]) -> MetricsSpec:
    """Validate a parsed YAML document."""
    if raw.get("version") != 1:
        raise SpecError("metrics.yml: version must be 1")
    metrics: list[Metric] = []
    for item in raw.get("metrics", []):
        name = str(item.get("name", ""))
        if not name:
            raise SpecError("a metric has no name")
        kind = str(item.get("kind", ""))
        if kind not in KINDS:
            raise SpecError(f"{name}: kind must be one of {KINDS}")
        scopes = {str(k): str(v) for k, v in (item.get("scopes") or {}).items()}
        for product, scope in scopes.items():
            if scope not in SCOPE_KINDS:
                raise SpecError(f"{name}: scope {scope!r} of {product} is not in {SCOPE_KINDS}")
        variants = tuple(_variant(v, name) for v in item.get("known_variants", []))
        metrics.append(
            Metric(
                name=name,
                description=str(item.get("description", "")),
                owner=str(item.get("owner", "")),
                entity=str(item.get("entity", "")),
                grain=tuple(str(g) for g in item.get("grain", [])),
                kind=kind,
                definition=dict(item.get("definition", {})),
                scopes=scopes,
                known_variants=variants,
            )
        )
    names = [m.name for m in metrics]
    if len(set(names)) != len(names):
        raise SpecError("duplicate metric names")
    spec = MetricsSpec(tuple(metrics))
    seen: set[tuple[str, str]] = set()
    for metric in spec.metrics:
        for v in metric.known_variants:
            if (v.product, v.reported_as) in seen:
                raise SpecError(f"{v.product}/{v.reported_as} is a variant of two metrics")
            seen.add((v.product, v.reported_as))
        if metric.kind in {"lapsed_between_windows", "rows_per_active"}:
            active = metric.definition.get("active_metric")
            if not isinstance(active, str) or spec.by_name(active).kind != (
                "distinct_entities_in_window"
            ):
                raise SpecError(f"{metric.name}: active_metric must name a window metric")
        spec.scopes_of(metric)
    return spec


def load_spec(path: Path) -> MetricsSpec:
    """Read and validate ``path``."""
    return parse_spec(yaml.safe_load(path.read_text(encoding="utf-8")))
