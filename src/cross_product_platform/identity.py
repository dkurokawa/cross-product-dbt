"""Identity matching: the single place that decides who is "the same person".

Every rule about *normalizing* a personal identifier and *deriving a join key*
from it lives here. The keys are computed once, at landing time, and travel with
the data as columns; SQL only ever joins on them (it never re-derives them, so the
salt never appears in a compiled dbt model under ``target/``).

Key derivation (design decision F4):

* ``member_key``   = HMAC-SHA256(salt, normalized e-mail) when the source has an e-mail;
  otherwise HMAC of the normalized phone; otherwise (product D) HMAC of phone + kana name;
  otherwise a source-local key that cannot match anything (counted as *unmatched*).
* ``link_key``     = HMAC of ``phone|kana name`` whenever both exist. It is the bridge that
  lets a product that only knows phone + kana (D) be attached to a person that other
  products know by e-mail.

The salt comes from the ``PLATFORM_HMAC_SALT`` environment variable. It is never logged
and never put into an exception message.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Final

SALT_ENV: Final = "PLATFORM_HMAC_SALT"

METHOD_EMAIL: Final = "email"
METHOD_PHONE: Final = "phone"
METHOD_PHONE_KANA: Final = "phone_kana"
METHOD_SOURCE_LOCAL: Final = "source_local"

_WHITESPACE = re.compile(r"[\s　]+")
_COMPANY_NOISE = re.compile(r"(株式会社|有限会社|\(株\)|\(有\)|[\s　])")


class MissingSaltError(RuntimeError):
    """Raised when the HMAC salt environment variable is not set."""


def get_salt() -> bytes:
    """Read the HMAC salt from the environment (never logged, never echoed)."""
    value = os.environ.get(SALT_ENV, "")
    if not value:
        # The message names the variable only; it must never contain a value.
        raise MissingSaltError(f"environment variable {SALT_ENV} must be set")
    return value.encode("utf-8")


def normalize_email(raw: str | None) -> str | None:
    """Lower-case, trim and strip a ``+tag`` from the local part; ``None`` if unusable."""
    if raw is None:
        return None
    text = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    if "@" not in text:
        return None
    local, _, domain = text.partition("@")
    local = local.split("+", 1)[0]
    if not local or not domain:
        return None
    return f"{local}@{domain}"


def normalize_phone(raw: str | None) -> str | None:
    """Digits only (full-width folded), ``+81`` rewritten to a leading ``0``."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", str(raw)))
    if digits.startswith("81") and len(digits) in (11, 12):
        digits = "0" + digits[2:]
    return digits if len(digits) >= 9 else None


def normalize_kana(raw: str | None) -> str | None:
    """Full-width katakana without spaces (half-width kana and hiragana are folded)."""
    if raw is None:
        return None
    text = unicodedata.normalize("NFKC", str(raw))
    text = _WHITESPACE.sub("", text)
    out = []
    for ch in text:
        code = ord(ch)
        out.append(chr(code + 0x60) if 0x3041 <= code <= 0x3096 else ch)
    result = "".join(out)
    return result or None


def normalize_company(raw: str | None) -> str | None:
    """Company name without legal-form words and spacing, NFKC-folded."""
    if raw is None:
        return None
    text = _COMPANY_NOISE.sub("", unicodedata.normalize("NFKC", str(raw)))
    return text or None


def hmac_hex(salt: bytes, purpose: str, value: str) -> str:
    """HMAC-SHA256 of ``purpose:value`` as lower-case hex."""
    return hmac.new(salt, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def account_key(name: str | None) -> str | None:
    """Stable key of a corporate account (company names are not personal data)."""
    norm = normalize_company(name)
    if norm is None:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Identity:
    """The join keys attached to one source record at landing time."""

    member_key: str
    key_method: str
    link_key: str | None


def phone_kana_key(salt: bytes, phone: str | None, kana: str | None) -> str | None:
    """Key over the normalized phone and kana name, if both are present."""
    p = normalize_phone(phone)
    k = normalize_kana(kana)
    if p is None or k is None:
        return None
    return hmac_hex(salt, "phone_kana", f"{p}|{k}")


def resolve_identity(
    salt: bytes,
    *,
    source: str,
    source_member_id: str,
    email: str | None = None,
    phone: str | None = None,
    kana: str | None = None,
) -> Identity:
    """Derive the ``member_key`` (and ``link_key``) for one source record.

    Priority: e-mail, then phone, then phone + kana. A record with none of them still
    gets a key (so it stays addressable) but with method ``source_local``; those are
    the *unmatched* records reported by the landing step.
    """
    link = phone_kana_key(salt, phone, kana)
    norm_email = normalize_email(email)
    if norm_email is not None:
        return Identity(hmac_hex(salt, "email", norm_email), METHOD_EMAIL, link)
    norm_phone = normalize_phone(phone)
    if norm_phone is not None and source != "D":
        return Identity(hmac_hex(salt, "phone", norm_phone), METHOD_PHONE, link)
    if link is not None:
        return Identity(link, METHOD_PHONE_KANA, link)
    return Identity(
        hmac_hex(salt, "source_local", f"{source}|{source_member_id}"),
        METHOD_SOURCE_LOCAL,
        None,
    )
