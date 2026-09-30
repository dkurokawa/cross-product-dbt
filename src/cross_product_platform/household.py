"""Household rules: the single place that decides which people share a household.

A household is *the person who pays plus the people who use the service*. The payer is
identified by the ``member_key`` the landing step derives for them; a household key is a
keyed hash of that, so two products that both know the same payer (by e-mail) produce
the same household key without ever exchanging an identifier.

Rules:

* A member registered with a guardian e-mail (a child account in the app, a junior
  membership at the gym) belongs to the household whose payer is that guardian.
* Everyone else is the payer of their own household.
"""

from __future__ import annotations

from .identity import hmac_hex, normalize_email


def payer_member_key(salt: bytes, *, own_member_key: str, guardian_email: str | None) -> str:
    """The ``member_key`` of the payer of this member's household."""
    guardian = normalize_email(guardian_email)
    if guardian is None:
        return own_member_key
    return hmac_hex(salt, "email", guardian)


def household_key(salt: bytes, *, own_member_key: str, guardian_email: str | None) -> str:
    """Household key for one member record (same payer, same key, in every product)."""
    payer = payer_member_key(salt, own_member_key=own_member_key, guardian_email=guardian_email)
    return hmac_hex(salt, "household", payer)
