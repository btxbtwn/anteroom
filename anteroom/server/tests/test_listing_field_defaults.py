from __future__ import annotations

import asyncio

import pytest

from anteroom.services import etsy_form, listing_generate, native_form, user_settings


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


def test_etsy_who_and_what_can_be_saved_as_defaults(settings_file):
    defaults = user_settings.set_listing_field_default(
        "etsy_specifics.whoMade", "Another company or person", "Another company or person",
    )
    defaults = user_settings.set_listing_field_default(
        "etsy_specifics.whatIsIt", "A finished product", "A finished product",
    )

    assert defaults["etsy_specifics.whoMade"] == {
        "value": "Another company or person", "label": "Another company or person",
    }
    assert defaults["etsy_specifics.whatIsIt"] == {
        "value": "A finished product", "label": "A finished product",
    }

    cleared = user_settings.clear_listing_field_default("etsy_specifics.whoMade")
    assert "etsy_specifics.whoMade" not in cleared
    assert "etsy_specifics.whatIsIt" in cleared


@pytest.mark.parametrize(("path", "value", "label"), [
    ("mercari_specifics.shipsFrom", "1234", None),
    ("mercari_specifics.shippingClassId", "", "USPS Ground Advantage"),
    ("mercari_specifics.shippingClassId", "2550", ""),
    ("etsy_specifics.whoMade", "My grandma", "My grandma"),
    ("etsy_specifics.whatIsIt", "A hat", "A hat"),
    ("etsy_specifics.renewalOption", "Yearly", "Yearly"),
    ("etsy_specifics.listingType", "Physical item", "Physical item"),
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


def test_generation_fills_empty_etsy_who_and_what_from_saved_defaults(settings_file):
    user_settings.set_listing_field_default(
        "etsy_specifics.whoMade", "Another company or person", "Another company or person",
    )
    user_settings.set_listing_field_default(
        "etsy_specifics.whatIsIt", "A finished product", "A finished product",
    )

    listing = {"title": "Tee", "etsy_specifics": {}}
    assert listing_generate.apply_send_readiness_fixes(listing) is True
    assert listing["etsy_specifics"]["who_made"] == "Another company or person"
    assert listing["etsy_specifics"]["what_is"] == "A finished product"

    listing["etsy_specifics"]["who_made"] = "I did"
    assert user_settings.apply_listing_field_defaults(listing) is False
    assert listing["etsy_specifics"]["who_made"] == "I did"


def test_etsy_form_starts_with_saved_who_and_what(settings_file):
    user_settings.set_listing_field_default(
        "etsy_specifics.whoMade", "Another company or person", "Another company or person",
    )
    user_settings.set_listing_field_default(
        "etsy_specifics.whatIsIt", "A finished product", "A finished product",
    )
    user_settings.set_listing_field_default(
        "etsy_specifics.renewalOption", "Automatic", "Automatic",
    )

    form = asyncio.run(etsy_form.editor_form({"etsy_specifics": {}, "direct_category_ids": {}}))
    rows = {row["path"]: row for row in form["fields"]}
    assert rows["etsy_specifics.whoMade"]["value"] == "Another company or person"
    assert rows["etsy_specifics.whoMade"]["save_as_default"] is True
    assert rows["etsy_specifics.whatIsIt"]["value"] == "A finished product"
    assert rows["etsy_specifics.whatIsIt"]["save_as_default"] is True
    assert rows["etsy_specifics.renewalOption"]["value"] == "Automatic"
    assert rows["etsy_specifics.renewalOption"]["save_as_default"] is True

    # A listing's own choice wins over the saved default.
    own = asyncio.run(etsy_form.editor_form({"etsy_specifics": {"renewalOption": "Manual"}, "direct_category_ids": {}}))
    own_rows = {row["path"]: row for row in own["fields"]}
    assert own_rows["etsy_specifics.renewalOption"]["value"] == "Manual"


def test_generation_fills_empty_etsy_renewal_from_saved_default(settings_file):
    user_settings.set_listing_field_default("etsy_specifics.renewalOption", "Automatic", "Automatic")

    listing = {"title": "Tee", "etsy_specifics": {}}
    assert listing_generate.apply_send_readiness_fixes(listing) is True
    assert listing["etsy_specifics"]["renewalOption"] == "Automatic"

    listing["etsy_specifics"]["renewalOption"] = "Manual"
    assert user_settings.apply_listing_field_defaults(listing) is False
    assert listing["etsy_specifics"]["renewalOption"] == "Manual"

    explicit_false = {"title": "Tee", "etsy_specifics": {"should_auto_renew": False}}
    assert user_settings.apply_listing_field_defaults(explicit_false) is False
    assert "renewalOption" not in explicit_false["etsy_specifics"]

    explicit_zero = {"title": "Tee", "etsy_specifics": {"should_auto_renew": 0}}
    assert user_settings.apply_listing_field_defaults(explicit_zero) is False
    assert "renewalOption" not in explicit_zero["etsy_specifics"]


SHOEBOX = "mercari_specifics.package.fitsShoebox"


@pytest.mark.parametrize(("value", "label", "stored", "shown"), [
    ("yes", "Yes", "yes", "Yes"),
    ("no", "No", "no", "No"),
    # The radio's label is the same answer as its value.
    ("Yes", None, "yes", "Yes"),
    (" NO ", None, "no", "No"),
])
def test_shoebox_answer_saves_reloads_and_clears(settings_file, value, label, stored, shown):
    defaults = user_settings.set_listing_field_default(SHOEBOX, value, label)

    assert defaults[SHOEBOX] == {"value": stored, "label": shown}
    # Read back from settings.json, the way the next request sees it.
    assert user_settings.get_listing_field_defaults()[SHOEBOX] == {"value": stored, "label": shown}
    assert user_settings.get_mercari_shoebox_default() == stored

    cleared = user_settings.clear_listing_field_default(SHOEBOX)
    assert SHOEBOX not in cleared
    assert user_settings.get_mercari_shoebox_default() is None


def test_saving_the_other_shoebox_answer_replaces_the_default(settings_file):
    user_settings.set_listing_field_default(SHOEBOX, "yes", "Yes")
    defaults = user_settings.set_listing_field_default(SHOEBOX, "no", "No")

    assert defaults[SHOEBOX] == {"value": "no", "label": "No"}


@pytest.mark.parametrize("value", ["", "maybe", "true", None])
def test_shoebox_default_accepts_only_yes_or_no(settings_file, value):
    with pytest.raises(ValueError):
        user_settings.set_listing_field_default(SHOEBOX, value, value)


def test_shoebox_default_keeps_the_other_saved_defaults(settings_file):
    user_settings.set_listing_field_default("etsy_specifics.whoMade", "I did", "I did")
    user_settings.set_listing_field_default(SHOEBOX, "no", "No")

    assert user_settings.clear_listing_field_default(SHOEBOX) == {
        "etsy_specifics.whoMade": {"value": "I did", "label": "I did"},
    }


def test_a_hand_edited_shoebox_setting_is_ignored(settings_file):
    settings_file.write_text('{"listing_field_defaults": {"mercari_specifics.package.fitsShoebox": {"value": "maybe"}}}')

    assert SHOEBOX not in user_settings.get_listing_field_defaults()
    assert user_settings.apply_mercari_shoebox_default({}) is False


@pytest.mark.parametrize("answer", ["yes", "no"])
def test_mercari_direct_form_starts_with_the_saved_shoebox_answer(settings_file, answer):
    user_settings.set_listing_field_default(SHOEBOX, answer)

    snapshot = native_form.attach_form({"mercari_specifics": {"package": {"pounds": 1}}}, "mercari")

    assert snapshot["mercari_specifics"]["package"] == {"pounds": 1, "fitsShoebox": answer}
    assert native_form.attach_form({}, "mercari")["mercari_specifics"]["package"] == {"fitsShoebox": answer}
    assert native_form.attach_form({"mercari_specifics": {"package": {"fitsShoebox": "  "}}}, "mercari")[
        "mercari_specifics"]["package"]["fitsShoebox"] == answer


@pytest.mark.parametrize(("saved", "own"), [
    ("yes", "no"), ("no", "yes"), ("yes", "No"), ("no", "Yes"), ("yes", " NO "), ("no", "YES"),
])
def test_a_listing_that_answers_the_shoebox_question_keeps_its_answer(settings_file, saved, own):
    user_settings.set_listing_field_default(SHOEBOX, saved)

    snapshot = native_form.attach_form({"mercari_specifics": {"package": {"fitsShoebox": own}}}, "mercari")

    assert snapshot["mercari_specifics"]["package"]["fitsShoebox"] == own


@pytest.mark.parametrize("own", [False, True, "maybe", "true", 0])
def test_a_shoebox_value_every_reader_treats_as_unanswered_takes_the_default(settings_file, own):
    user_settings.set_listing_field_default(SHOEBOX, "no")

    snapshot = native_form.attach_form({"mercari_specifics": {"package": {"fitsShoebox": own}}}, "mercari")

    assert snapshot["mercari_specifics"]["package"]["fitsShoebox"] == "no"


def test_shoebox_default_applies_only_to_mercari(settings_file):
    user_settings.set_listing_field_default(SHOEBOX, "yes")

    snapshot = native_form.attach_form({"title": "Tee", "price": 18}, "poshmark")

    assert "package" not in (snapshot.get("mercari_specifics") or {})


def test_generation_does_not_write_the_shoebox_default_into_the_listing(settings_file):
    user_settings.set_listing_field_default(SHOEBOX, "yes")

    listing = {"title": "Tee", "mercari_specifics": {}}
    listing_generate.apply_send_readiness_fixes(listing)

    assert "package" not in listing["mercari_specifics"]


def test_mercari_editor_form_shows_the_saved_shoebox_answer_and_offers_the_bookmark(settings_file):
    from anteroom.services import mercari_form

    user_settings.set_listing_field_default(SHOEBOX, "no")

    rows = {row["path"]: row for row in mercari_form.form(native_form.attach_form({}, "mercari"))["fields"]}
    assert rows[SHOEBOX]["value"] == "no"
    assert rows[SHOEBOX]["save_as_default"] is True

    own = native_form.attach_form({"mercari_specifics": {"package": {"fitsShoebox": "Yes"}}}, "mercari")
    rows = {row["path"]: row for row in mercari_form.form(own)["fields"]}
    # #1303: a saved radio label reads as its value.
    assert rows[SHOEBOX]["value"] == "yes"
