from __future__ import annotations

import pytest

from vendoo_studio.services import native_form, user_settings


@pytest.fixture
def settings_file(monkeypatch, tmp_path):
    """Each test reads and writes its own settings.json."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(user_settings, "settings_path", lambda: path)
    return path


def test_saving_the_ships_from_field_uses_the_existing_zip_setting(settings_file):
    defaults = user_settings.set_listing_field_default("mercari_specifics.shipsFrom", "70125")

    assert defaults["mercari_specifics.shipsFrom"] == {"value": "70125", "label": "70125"}
    assert user_settings.get_ships_from_zip() == "70125"
    assert user_settings.clear_listing_field_default("mercari_specifics.shipsFrom") == {}
    assert user_settings.get_ships_from_zip() == ""


def test_shipping_label_keeps_the_id_and_the_label_the_seller_picked(settings_file):
    defaults = user_settings.set_listing_field_default(
        "mercari_specifics.shippingClassId", "2550", "USPS Ground Advantage",
    )

    assert defaults["mercari_specifics.shippingClassId"] == {
        "value": "2550",
        "label": "USPS Ground Advantage",
    }
    assert user_settings.get_mercari_shipping_label() == {
        "id": "2550",
        "label": "USPS Ground Advantage",
    }

    assert user_settings.clear_listing_field_default("mercari_specifics.shippingClassId") == {}
    assert user_settings.get_mercari_shipping_label() is None


@pytest.mark.parametrize(("path", "value", "label"), [
    ("mercari_specifics.shipsFrom", "1234", None),
    ("mercari_specifics.shippingClassId", "", "USPS Ground Advantage"),
    ("mercari_specifics.shippingClassId", "2550", ""),
    ("title", "A tee", None),
])
def test_only_the_fields_that_can_be_saved_as_defaults_accept_a_value(settings_file, path, value, label):
    with pytest.raises(ValueError):
        user_settings.set_listing_field_default(path, value, label)


def test_mercari_direct_form_starts_with_the_saved_ships_from_and_label(settings_file):
    user_settings.set_listing_field_default("mercari_specifics.shipsFrom", "70125")
    user_settings.set_listing_field_default(
        "mercari_specifics.shippingClassId", "2550", "USPS Ground Advantage",
    )

    snapshot = native_form.attach_form({"mercari_specifics": {}}, "mercari")

    assert snapshot["mercari_specifics"]["shipsFrom"] == "70125"
    assert snapshot["form"]["ships_from_zip"] == "70125"
    assert snapshot["form"]["mercari_shipping_label"] == {
        "id": "2550",
        "label": "USPS Ground Advantage",
    }


def test_a_listing_that_already_answers_a_field_keeps_its_own_value(settings_file):
    user_settings.set_listing_field_default("mercari_specifics.shipsFrom", "70125")

    snapshot = native_form.attach_form({"mercari_specifics": {"shipsFrom": "90210"}}, "mercari")

    assert snapshot["mercari_specifics"]["shipsFrom"] == "90210"


def test_vendoo_mercari_form_uses_the_saved_zip_for_ships_from(settings_file):
    user_settings.set_listing_field_default("mercari_specifics.shipsFrom", "70125")

    form = native_form.vendoo_form(
        {"title": "Tee", "description": "Soft cotton", "price": 18, "brand": "Hanes", "condition": "Good"},
        "mercari",
    )

    assert form["specifics"]["mercariLocalInformation"] == "70125"
