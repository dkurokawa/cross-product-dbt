"""The synthetic world: people, households and which product each of them uses.

All five sources are generated from this one world, so the same person (and the same
household) really does appear in several products, each time written the way that
product writes it. Nothing here refers to a real person or organization.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache
from typing import Any, Final, cast

import numpy as np
import pandas as pd
from faker import Faker

from ..config import GeneratorConfig

PRODUCTS: Final = ("A", "B", "C", "D")

_EMAIL_STEMS = (
    "sky", "leaf", "moon", "river", "stone", "cloud", "wave", "pine", "fox", "owl",
    "tea", "rain", "star", "wind", "ember", "coral", "maple", "lark", "dune", "fern",
)  # fmt: skip
_EMAIL_DOMAINS = ("example.com", "example.net", "example.org", "example.jp")
_HEALTH_NOTES = (
    "Chronic lower back pain; avoid heavy deadlifts.",
    "Left knee ligament injury in 2025, rehabilitation ongoing.",
    "Mild asthma, carries an inhaler.",
    "Shoulder dislocation last spring; no overhead pressing.",
    "High blood pressure, checked by a doctor every quarter.",
    "Recovering from a wrist fracture.",
    "Pollen allergy; prefers indoor classes in spring.",
    "Occasional dizziness when training on an empty stomach.",
)


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Rows as plain dicts (values are ``Any``; avoids pandas-stubs' huge row unions)."""
    return cast("list[dict[str, Any]]", frame.to_dict("records"))


def _to_hiragana(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in text)


@cache
def _halfwidth_table() -> dict[str, str]:
    table: dict[str, str] = {}
    for code in range(0xFF66, 0xFF9E):
        half = chr(code)
        table.setdefault(unicodedata.normalize("NFKC", half), half)
        for mark in ("ﾞ", "ﾟ"):
            combined = unicodedata.normalize("NFKC", half + mark)
            if len(combined) == 1:
                table.setdefault(combined, half + mark)
    return table


def to_halfwidth_kana(text: str) -> str:
    """Katakana to half-width katakana (the way the acquired billing software prints it)."""
    table = _halfwidth_table()
    return "".join(table.get(c, c) for c in text)


def to_fullwidth_digits(text: str) -> str:
    """ASCII digits and hyphen to their full-width forms."""
    return text.translate(str.maketrans("0123456789-", "０１２３４５６７８９－"))


def kana_variants(katakana: str, family_len: int) -> dict[str, str]:
    """A kana name in the four notations the products use.

    ``family_len`` is the length of the family-name part, so a space can be put between
    family and given name in the notations that have one.
    """
    family, given = katakana[:family_len], katakana[family_len:]
    return {
        "hiragana": _to_hiragana(family + given),
        "spaced": f"{family} {given}",
        "plain": family + given,
        "halfwidth": to_halfwidth_kana(f"{family} {given}"),
    }


@dataclass(frozen=True)
class World:
    """People plus product memberships (immutable after :func:`build_world`)."""

    cfg: GeneratorConfig
    persons: pd.DataFrame
    memberships: pd.DataFrame

    def members(self, product: str) -> pd.DataFrame:
        """Memberships of one product joined with the person attributes."""
        m = self.memberships[self.memberships["product"] == product]
        return m.merge(self.persons, on="person_id", how="left").reset_index(drop=True)


def _make_persons(cfg: GeneratorConfig, rng: np.random.Generator) -> pd.DataFrame:
    fake = Faker("ja_JP")
    Faker.seed(cfg.seed)
    n = cfg.n_persons
    n_children = int(n * 0.12)
    is_child = np.zeros(n, dtype=bool)
    is_child[n - n_children :] = True

    rows: list[dict[str, object]] = []
    for pid in range(n):
        family = fake.last_name()
        given = fake.first_name()
        fam_kana = fake.last_kana_name()
        giv_kana = fake.first_kana_name()
        child = bool(is_child[pid])
        stem = _EMAIL_STEMS[int(rng.integers(len(_EMAIL_STEMS)))]
        domain = _EMAIL_DOMAINS[int(rng.integers(len(_EMAIL_DOMAINS)))]
        has_email = (not child) or rng.random() < 0.5
        has_phone = not child
        if child:
            age = int(rng.integers(6, 17))
            birth = date(2026, 1, 1) - timedelta(days=int(age * 365 + rng.integers(0, 365)))
        else:
            age = int(rng.integers(19, 72))
            birth = date(2026, 1, 1) - timedelta(days=int(age * 365 + rng.integers(0, 365)))
        health = (
            _HEALTH_NOTES[int(rng.integers(len(_HEALTH_NOTES)))] if rng.random() < 0.15 else None
        )
        rows.append(
            {
                "person_id": pid,
                "name": f"{family} {given}",
                "kana": fam_kana + giv_kana,
                "kana_family_len": len(fam_kana),
                "email": f"{stem}{pid:05d}@{domain}" if has_email else None,
                "phone": f"090{int(rng.integers(10_000_000, 100_000_000))}" if has_phone else None,
                "birth_date": birth,
                "is_child": child,
                "guardian_person_id": -1,
                "health_note": health,
            }
        )
    persons = pd.DataFrame(rows)
    adults = np.flatnonzero(~persons["is_child"].to_numpy())
    kids = np.flatnonzero(persons["is_child"].to_numpy())
    persons.loc[kids, "guardian_person_id"] = rng.choice(adults, size=len(kids))
    return persons


def _make_memberships(
    cfg: GeneratorConfig, rng: np.random.Generator, persons: pd.DataFrame
) -> pd.DataFrame:
    prim_p = np.array([0.40, 0.28, 0.27, 0.05])
    n = len(persons)
    primary = rng.choice(len(PRODUCTS), size=n, p=prim_p)
    extra = rng.random(n) < cfg.overlap
    second = rng.integers(0, len(PRODUCTS), size=n)
    is_child = persons["is_child"].to_numpy()
    guardian = persons["guardian_person_id"].to_numpy()

    member_sets: list[set[int]] = []
    for i in range(n):
        s = {int(primary[i])}
        if extra[i]:
            s.add(int(second[i]))
        member_sets.append(s)
    # Children only exist where their guardian is a member (families use A and C).
    for child in np.flatnonzero(is_child):
        guardian_products = member_sets[int(guardian[child])]
        allowed = {p for p in guardian_products if PRODUCTS[p] in ("A", "C")}
        member_sets[int(child)] = {p for p in allowed if rng.random() < 0.85}

    period_days = (cfg.end - cfg.start).days + 1
    rows: list[dict[str, Any]] = []
    for i in range(n):
        for p in sorted(member_sets[i]):
            if rng.random() < 0.55:
                joined = cfg.start - timedelta(days=int(rng.integers(1, 121)))
            else:
                joined = cfg.start + timedelta(days=int(rng.integers(0, int(period_days * 0.9))))
            if is_child[i]:
                joined = max(joined, cfg.start - timedelta(days=60))
            left = None
            if rng.random() < 0.25:
                earliest = max(joined + timedelta(days=45), cfg.start + timedelta(days=30))
                if earliest < cfg.end:
                    left = earliest + timedelta(
                        days=int(rng.integers(0, (cfg.end - earliest).days))
                    )
            rows.append(
                {
                    "product": PRODUCTS[p],
                    "person_id": i,
                    "joined_on": joined,
                    "left_on": left,
                }
            )
    # A guardian is registered before the child in the same product.
    first_join = {(str(r["product"]), int(r["person_id"])): r["joined_on"] for r in rows}
    for r in rows:
        if is_child[int(r["person_id"])]:
            guardian_join = first_join.get((str(r["product"]), int(guardian[int(r["person_id"])])))
            if guardian_join is not None and r["joined_on"] <= guardian_join:
                r["joined_on"] = guardian_join + timedelta(days=1)
                left_on = r["left_on"]
                if left_on is not None and left_on <= guardian_join:
                    r["left_on"] = None
    ms = pd.DataFrame(rows)
    return ms


def build_world(cfg: GeneratorConfig) -> World:
    """Create the people and memberships for ``cfg`` (deterministic in ``cfg.seed``)."""
    rng = np.random.default_rng(cfg.seed)
    persons = _make_persons(cfg, rng)
    memberships = _make_memberships(cfg, rng, persons)
    return World(cfg=cfg, persons=persons, memberships=memberships)


def span_bounds(
    cfg: GeneratorConfig, joined: pd.Series, left: pd.Series
) -> tuple[np.ndarray, np.ndarray]:
    """Active day range (offsets from ``cfg.start``, inclusive) of each membership."""
    start = pd.Timestamp(cfg.start)
    lo = (pd.to_datetime(joined) - start).dt.days.clip(lower=0).to_numpy()
    end_off = (pd.Timestamp(cfg.end) - start).days
    left_ts = pd.to_datetime(left)
    hi = ((left_ts - start).dt.days.fillna(end_off)).clip(upper=end_off).to_numpy()
    return lo.astype(int), np.maximum(hi.astype(int), lo.astype(int))


def sample_days(
    rng: np.random.Generator,
    lo: np.ndarray,
    hi: np.ndarray,
    per_member: np.ndarray,
    start: date,
    weekend_weight: float = 0.6,
) -> tuple[np.ndarray, np.ndarray]:
    """Draw event days for each member; returns ``(member_index, day_offset)`` arrays.

    Days are uniform inside each member's active span and thinned on weekends, so the
    daily volume has a weekly rhythm (which the row-count anomaly test has to cope with).
    """
    idx = np.repeat(np.arange(len(lo)), per_member)
    span = (hi - lo + 1)[idx]
    off = lo[idx] + np.floor(rng.random(len(idx)) * span).astype(int)
    dow = (pd.Timestamp(start).dayofweek + off) % 7
    keep = rng.random(len(idx)) < np.where(dow >= 5, weekend_weight, 1.0)
    return idx[keep], off[keep]
