import pytest

from cross_product_platform import household, identity
from cross_product_platform.identity import (
    METHOD_EMAIL,
    METHOD_PHONE,
    METHOD_PHONE_KANA,
    METHOD_SOURCE_LOCAL,
    MissingSaltError,
    account_key,
    get_salt,
    hmac_hex,
    normalize_company,
    normalize_email,
    normalize_kana,
    normalize_phone,
    resolve_identity,
)

SALT = b"test-salt"


def test_email_normalization() -> None:
    assert normalize_email("  Sky00001+news@Example.COM ") == "sky00001@example.com"
    assert normalize_email("ＡＢＣ@Example.com") == "abc@example.com"
    assert normalize_email("no-at-sign") is None
    assert normalize_email("@example.com") is None
    assert normalize_email(None) is None


def test_phone_normalization() -> None:
    assert normalize_phone("090-1234-5678") == "09012345678"
    assert normalize_phone("０９０－１２３４－５６７８") == "09012345678"
    assert normalize_phone("+81 90 1234 5678") == "09012345678"
    assert normalize_phone("123") is None
    assert normalize_phone(None) is None


def test_kana_normalization_folds_all_notations() -> None:
    forms = ["さとう しょうた", "サトウ ショウタ", "サトウショウタ", "ｻﾄｳ ｼｮｳﾀ", "サトウ　ショウタ"]
    assert {normalize_kana(f) for f in forms} == {"サトウショウタ"}
    assert normalize_kana(None) is None
    assert normalize_kana("  ") is None


def test_company_normalization_and_account_key() -> None:
    forms = ["株式会社ミラタ商事", "ミラタ商事株式会社", "(株)ミラタ商事", "ミラタ 商事"]
    assert len({normalize_company(f) for f in forms}) == 1
    assert len({account_key(f) for f in forms}) == 1
    assert account_key(None) is None
    assert normalize_company("株式会社") is None


def test_identity_priority_email_then_phone_then_phone_kana() -> None:
    by_email = resolve_identity(
        SALT, source="A", source_member_id="1", email="a@example.com", phone="09012345678",
        kana="サトウ",
    )  # fmt: skip
    assert by_email.key_method == METHOD_EMAIL
    assert by_email.member_key == hmac_hex(SALT, "email", "a@example.com")
    assert by_email.link_key is not None

    by_phone = resolve_identity(SALT, source="C", source_member_id="1", phone="09012345678")
    assert by_phone.key_method == METHOD_PHONE
    assert by_phone.link_key is None  # no kana, so no bridge key

    d = resolve_identity(
        SALT, source="D", source_member_id="INV1", phone="０９０１２３４５６７８", kana="ｻﾄｳ"
    )
    assert d.key_method == METHOD_PHONE_KANA
    assert d.member_key == d.link_key

    local = resolve_identity(SALT, source="A", source_member_id="child-1")
    assert local.key_method == METHOD_SOURCE_LOCAL
    assert local.link_key is None


def test_d_and_email_products_share_a_link_key() -> None:
    a = resolve_identity(
        SALT, source="A", source_member_id="1", email="a@example.com", phone="090-1234-5678",
        kana="さとう しょうた",
    )  # fmt: skip
    d = resolve_identity(
        SALT,
        source="D",
        source_member_id="INV9",
        phone="０９０－１２３４－５６７８",
        kana="ｻﾄｳ ｼｮｳﾀ",
    )
    assert a.link_key == d.link_key


def test_keys_depend_on_salt() -> None:
    a = resolve_identity(b"salt-1", source="A", source_member_id="1", email="a@example.com")
    b = resolve_identity(b"salt-2", source="A", source_member_id="1", email="a@example.com")
    assert a.member_key != b.member_key


def test_missing_salt_error_never_contains_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(identity.SALT_ENV, raising=False)
    with pytest.raises(MissingSaltError) as err:
        get_salt()
    assert identity.SALT_ENV in str(err.value)
    monkeypatch.setenv(identity.SALT_ENV, "s3cret-value")
    assert get_salt() == b"s3cret-value"


def test_household_same_payer_same_key_across_products() -> None:
    parent = resolve_identity(SALT, source="A", source_member_id="1", email="p@example.com")
    parent_c = resolve_identity(SALT, source="C", source_member_id="9", email=" P@example.com ")
    own = household.household_key(SALT, own_member_key=parent.member_key, guardian_email=None)
    own_c = household.household_key(SALT, own_member_key=parent_c.member_key, guardian_email=None)
    child = resolve_identity(SALT, source="A", source_member_id="2")
    kid = household.household_key(
        SALT, own_member_key=child.member_key, guardian_email="P@example.com"
    )
    assert own == own_c == kid
    assert (
        household.payer_member_key(
            SALT, own_member_key=child.member_key, guardian_email="P@example.com"
        )
        == parent.member_key
    )
