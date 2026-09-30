"""Product A (consumer app) event types, written through eventlake.

``member_key`` / ``link_key`` / ``key_method`` / ``household_key`` / ``ingested_at`` are
left empty by the app; the landing step fills them in (see :mod:`..landing`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar
from uuid import UUID

from eventlake import Event


class MemberRegistered(Event):
    """A member (or a child registered by a guardian) created an app account."""

    event_type: ClassVar[str] = "member_registered"

    member_id: UUID
    email: str | None = None
    name: str
    name_kana: str | None = None
    phone: str | None = None
    birth_date: date
    guardian_member_id: UUID | None = None
    guardian_email: str | None = None
    #: Free-text injury / condition declaration (sensitive personal information).
    health_notes: str | None = None
    member_key: str | None = None
    link_key: str | None = None
    key_method: str | None = None
    household_key: str | None = None
    ingested_at: datetime | None = None


class AppOpened(Event):
    """The app was opened."""

    event_type: ClassVar[str] = "app_opened"

    member_id: UUID
    ingested_at: datetime | None = None


class WorkoutCompleted(Event):
    """A workout was completed (logged in the app)."""

    event_type: ClassVar[str] = "workout_completed"

    member_id: UUID
    workout_type: str
    duration_min: int
    ingested_at: datetime | None = None


class PurchaseMade(Event):
    """An in-app purchase. The amount is tax-inclusive."""

    event_type: ClassVar[str] = "purchase_made"

    member_id: UUID
    sku: str
    amount_tax_incl: int
    tax_rate: float
    ingested_at: datetime | None = None
