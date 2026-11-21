"""Sellers keep the current description formula and can add or remove sections."""

import pytest
from fastapi.testclient import TestClient

from anteroom.main import app
from anteroom.services import skill_formulas


@pytest.fixture(autouse=True)
def _reset_listing_formulas():
    from anteroom.services.user_settings import set_listing_formulas

    set_listing_formulas(title="", description="")
    yield
    set_listing_formulas(title="", description="")


def test_default_blocks_are_the_current_description_formula():
    compiled = skill_formulas.compile_description_blocks(skill_formulas.default_description_block_ids())
    assert compiled == skill_formulas.DEFAULT_DESCRIPTION_FORMULA
    assert skill_formulas.description_formula_markers() == ("measurements:",)


def test_formula_settings_list_current_sections_and_extras():
    body = TestClient(app).get("/api/settings/formulas").json()
    enabled = [block["id"] for block in body["blocks"] if block["enabled"]]
    assert enabled == list(skill_formulas.default_description_block_ids())
    assert body["custom_description"] is False
    labels = {block["id"]: block["label"] for block in body["blocks"]}
    assert labels["vibe"] == "Vibe"
    assert labels["flaws"] == "Flaws"
    assert labels["measurements"] == "Measurements"
    for extra in ("trend", "fit", "material", "pattern", "occasion", "details", "care", "home", "shipping"):
        assert labels[extra]
        assert extra not in enabled


def test_seller_can_remove_measurements_and_add_material():
    client = TestClient(app)
    saved = client.put(
        "/api/settings/formulas",
        json={"blocks": ["vibe", "flaws", "material"]},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert "Measurements:" not in body["description"]
    assert "Material:" in body["description"]
    enabled = [block["id"] for block in body["blocks"] if block["enabled"]]
    assert enabled == ["vibe", "flaws", "material"]
    assert body["description"].index("Flaws:") < body["description"].index("Material:")
    assert skill_formulas.description_formula_markers() == ()
    assert "Material:" in skill_formulas.listing_formula_rules()
    assert skill_formulas.description_lists_flaws() is True

    listing = {"description": "Y2K denim shorts with a soft wash."}
    assert skill_formulas.description_formula_markers() == ()
    from anteroom.services.listing_generate import ensure_physical_description

    assert ensure_physical_description(listing) is False
    assert "Measurements:" not in listing["description"]


def test_clearing_every_section_does_not_restore_the_default():
    client = TestClient(app)
    saved = client.put("/api/settings/formulas", json={"blocks": []})
    assert saved.status_code == 200, saved.text
    assert saved.json()["description"] != skill_formulas.DEFAULT_DESCRIPTION_FORMULA
    assert saved.json()["blocks"]
    assert all(block["enabled"] is False for block in saved.json()["blocks"])
    assert skill_formulas.description_formula_markers() == ()


def test_unknown_description_section_is_rejected():
    response = TestClient(app).put("/api/settings/formulas", json={"blocks": ["vibe", "price"]})
    assert response.status_code == 400


def test_typed_formula_stays_in_place_until_sections_are_chosen():
    client = TestClient(app)
    typed = "Short pitch.\n\nNotes: {detail}"
    saved = client.put("/api/settings/formulas", json={"description": typed})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["description"] == typed
    assert body["custom_description"] is True
    assert [block["id"] for block in body["blocks"] if block["enabled"]] == []
    assert skill_formulas.description_formula_markers() == ("notes:",)


def test_turned_off_sections_are_not_written_back():
    from anteroom.services.listing_generate import apply_seller_description_facts
    from anteroom.services.user_settings import set_listing_formulas

    seller = {
        "garment": "top",
        "measurements": {"top": {"pitToPit": "20", "length": "26", "sleeve": "8"}},
        "knownFlaws": "small stain on hem",
    }
    set_listing_formulas(description=skill_formulas.compile_description_blocks(["vibe", "flaws"]))
    listing = {"description": "Y2K denim shorts with a soft worn wash."}
    apply_seller_description_facts(listing, seller)
    assert "Measurements:" not in listing["description"]
    assert "small stain on hem" in listing["description"]

    set_listing_formulas(description=skill_formulas.compile_description_blocks(["vibe", "measurements"]))
    listing = {"description": "Y2K denim shorts with a soft worn wash."}
    apply_seller_description_facts(listing, seller)
    assert "Flaws:" not in listing["description"]
    assert 'Pit to pit: 20"' in listing["description"]


def test_readiness_rewrite_keeps_sections_the_seller_added():
    from anteroom.services.listing_generate import ensure_physical_description
    from anteroom.services.user_settings import set_listing_formulas

    set_listing_formulas(
        description=skill_formulas.compile_description_blocks(
            ["vibe", "condition", "care", "material", "measurements"]
        )
    )
    original = (
        "Y2K black cotton tee with a soft wash and a straight hem.\n\n"
        "Condition: Pre-owned, light wear.\n\n"
        "Care: Machine wash cold.\n\n"
        'Measurements: Pit to pit: 20"; Length: 26"'
    )
    listing = {"description": original}
    assert ensure_physical_description(listing) is False
    assert listing["description"] == original

    missing = (
        "Y2K black cotton tee with a soft wash and a straight hem.\n\n"
        "Condition: Pre-owned, light wear.\n\n"
        "Care: Machine wash cold."
    )
    listing = {"description": missing}
    assert ensure_physical_description(listing) is True
    assert "Condition: Pre-owned, light wear." in listing["description"]
    assert "Care: Machine wash cold." in listing["description"]
    assert "Measurements:" in listing["description"]


def test_prompt_allows_sections_the_seller_turned_on():
    from anteroom.services.user_settings import set_listing_formulas

    set_listing_formulas(
        description=skill_formulas.compile_description_blocks(["vibe", "condition", "shipping"])
    )
    rules = skill_formulas.listing_formula_rules()
    assert "Do not add Size, a full condition write-up, shipping speed" not in rules
    assert "Shipping:" in rules
    assert "Condition:" in rules
    assert skill_formulas.description_formula_markers() == ("condition:",)


def test_clearing_every_section_accepts_one_sentence():
    from anteroom.services.user_settings import set_listing_formulas
    from anteroom.services.validation import _description_follows_formula

    set_listing_formulas(description=skill_formulas.NO_SECTION_DESCRIPTION)
    sentence = "Black cotton tee with a soft wash and a straight hem."
    assert _description_follows_formula(sentence) is True


def test_readiness_separates_inline_measurements_without_losing_custom_prose():
    from anteroom.services.listing_generate import apply_send_readiness_fixes
    from anteroom.services.user_settings import set_listing_formulas
    from anteroom.services.validation import _description_follows_formula

    set_listing_formulas(description="{custom prose}\n\nMeasurements: {measurements}")
    listing = {"description": "Soft black cotton tee. Measurements: 20 inches."}
    assert not _description_follows_formula(listing["description"])
    assert apply_send_readiness_fixes(listing)
    assert listing["description"] == "Soft black cotton tee.\n\nMeasurements: 20 inches."
    assert _description_follows_formula(listing["description"])
    original = listing["description"]
    apply_send_readiness_fixes(listing)
    assert listing["description"] == original


@pytest.mark.parametrize("flaws_label,measurements_label", [("Flaw", "Measurement"), ("Flaws", "Measurements")])
def test_typed_headings_receive_seller_facts(flaws_label, measurements_label):
    from anteroom.services.listing_generate import apply_seller_description_facts
    from anteroom.services.user_settings import set_listing_formulas
    from anteroom.services.validation import _description_follows_formula

    set_listing_formulas(
        description=f"{{prose}}\n\n{flaws_label}: {{flaw}}\n\n{measurements_label}: {{measurements}}"
    )
    listing = {
        "description": f"Soft black cotton tee.\n\n{flaws_label}: None.\n\n{measurements_label}: See photos"
    }
    assert apply_seller_description_facts(
        listing, {"knownFlaws": "small stain on hem", "descriptionMeasurements": "20 inches"}
    )
    assert f"{flaws_label}: small stain on hem." in listing["description"]
    assert f"{measurements_label}: 20 inches" in listing["description"]
    assert _description_follows_formula(listing["description"])
    assert not apply_seller_description_facts(
        listing, {"knownFlaws": "small stain on hem", "descriptionMeasurements": "20 inches"}
    )


def test_typed_material_is_required_but_catalog_material_is_optional():
    from anteroom.services.user_settings import set_listing_formulas
    from anteroom.services.validation import _description_follows_formula

    set_listing_formulas(description="Material: {composition}\n\nNotes: {detail}")
    description = "Soft black tee with a straight hem.\n\nNotes: Light wear."
    assert skill_formulas.description_formula_markers() == ("material:", "notes:")
    assert not _description_follows_formula(description)
    assert _description_follows_formula(f"Material: Cotton\n\n{description}")

    set_listing_formulas(description=skill_formulas.compile_description_blocks(["vibe", "material"]))
    assert skill_formulas.description_formula_markers() == ()
    assert _description_follows_formula(description)


@pytest.mark.parametrize("blocks", [["vibe", "material"], ["vibe", "flaws", "material"], ["material"]])
@pytest.mark.parametrize("description", ["Black cotton tee with a straight hem.", "Black tee."])
def test_omitted_optional_blocks_do_not_require_multiline_layout(blocks, description):
    from anteroom.services.user_settings import set_listing_formulas
    from anteroom.services.validation import _description_follows_formula

    set_listing_formulas(description=skill_formulas.compile_description_blocks(blocks))
    assert _description_follows_formula(description)


@pytest.mark.parametrize(
    "template",
    [skill_formulas.DEFAULT_DESCRIPTION_FORMULA, "{custom prose}\n\nMeasurements: {measurements}"],
)
def test_required_multiline_layout_is_preserved(template):
    from anteroom.services.user_settings import set_listing_formulas
    from anteroom.services.validation import _description_follows_formula

    set_listing_formulas(description=template)
    assert not _description_follows_formula("Soft black cotton tee. Measurements: 20 inches.")
    assert _description_follows_formula("Soft black cotton tee.\n\nMeasurements: 20 inches.")


def test_reordered_description_blocks_persist_and_round_trip():
    client = TestClient(app)
    desired = ["measurements", "material", "vibe", "flaws"]
    saved = client.put("/api/settings/formulas", json={"blocks": desired})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["custom_description"] is False
    assert body["default_blocks"] == ["vibe", "flaws", "measurements"]
    assert [block["id"] for block in body["blocks"] if block["enabled"]] == desired
    assert (
        body["description"].index("Measurements:")
        < body["description"].index("Material:")
        < body["description"].index("{trendy vibe/style keyword sentence with period}")
        < body["description"].index("Flaws:")
    )
    assert "in that order" in skill_formulas.listing_formula_rules()

    reloaded = client.get("/api/settings/formulas").json()
    assert reloaded["custom_description"] is False
    assert reloaded["default_blocks"] == ["vibe", "flaws", "measurements"]
    assert [block["id"] for block in reloaded["blocks"] if block["enabled"]] == desired

    # Reordering only the default three still saves as a custom order, not the default string.
    reordered_default = client.put("/api/settings/formulas", json={"blocks": ["measurements", "vibe", "flaws"]}).json()
    assert reordered_default["description"] != ""
    assert [block["id"] for block in reordered_default["blocks"] if block["enabled"]] == [
        "measurements",
        "vibe",
        "flaws",
    ]

    # Saving the default blocks in default order clears custom description back to built-in default.
    reset = client.put("/api/settings/formulas", json={"blocks": ["vibe", "flaws", "measurements"]}).json()
    assert reset["description"] == ""
    assert [block["id"] for block in reset["blocks"] if block["enabled"]] == ["vibe", "flaws", "measurements"]


def test_duplicate_block_ids_deduplicate_preserving_first_position():
    client = TestClient(app)
    saved = client.put("/api/settings/formulas", json={"blocks": ["material", "vibe", "material", "flaws"]})
    assert saved.status_code == 200, saved.text
    enabled = [block["id"] for block in saved.json()["blocks"] if block["enabled"]]
    assert enabled == ["material", "vibe", "flaws"]


def test_match_description_blocks_rejects_duplicate_snippets():
    snippet = next(b.snippet for b in skill_formulas.DESCRIPTION_BLOCKS if b.id == "vibe")
    assert skill_formulas.match_description_blocks(f"{snippet}\n\n{snippet}") is None


def test_seller_facts_respect_custom_section_order():
    from anteroom.services.listing_generate import apply_seller_description_facts, ensure_physical_description
    from anteroom.services.user_settings import set_listing_formulas

    seller = {
        "garment": "top",
        "measurements": {"top": {"pitToPit": "20", "length": "26"}},
        "knownFlaws": "small stain on hem",
    }

    # Measurements first: measurements, vibe, flaws — both when Flaws is present and when Flaws is absent.
    set_listing_formulas(
        description=skill_formulas.compile_description_blocks(["measurements", "vibe", "flaws"])
    )
    with_flaws = {"description": "Y2K black cotton tee with a soft wash.\n\nFlaws:\n- Small stain on hem."}
    apply_seller_description_facts(with_flaws, seller)
    assert with_flaws["description"].startswith('Measurements: Pit to pit: 20"; Length: 26"\n\nY2K black cotton tee')
    assert (
        with_flaws["description"].index("Measurements:")
        < with_flaws["description"].index("Y2K black cotton tee")
        < with_flaws["description"].index("Flaws:")
    )

    without_flaws = {"description": "Y2K black cotton tee with a soft wash."}
    apply_seller_description_facts(without_flaws, {"garment": "top", "measurements": seller["measurements"]})
    assert without_flaws["description"].startswith('Measurements: Pit to pit: 20"; Length: 26"\n\nY2K black cotton tee')

    ensure_listing = {"description": "Y2K black cotton tee with a soft wash.\n\nFlaws:\n- Small stain on hem."}
    assert ensure_physical_description(ensure_listing) is True
    assert ensure_listing["description"].startswith("Measurements: See photos\n\nY2K black cotton tee")

    # Intermediate attributes: vibe, flaws, material, measurements.
    set_listing_formulas(
        description=skill_formulas.compile_description_blocks(["vibe", "flaws", "material", "measurements"])
    )
    with_material = {
        "description": 'Y2K black cotton tee.\n\nMaterial: 100% cotton.\n\nMeasurements: Pit to pit: 20"; Length: 26"'
    }
    apply_seller_description_facts(with_material, seller)
    assert (
        with_material["description"].index("Y2K black cotton tee.")
        < with_material["description"].index("Flaws:")
        < with_material["description"].index("Material:")
        < with_material["description"].index("Measurements:")
    )

    # Material before Measurements before Vibe: material, measurements, vibe, flaws.
    set_listing_formulas(
        description=skill_formulas.compile_description_blocks(["material", "measurements", "vibe", "flaws"])
    )
    middle_measurements = {
        "description": "Material: 100% cotton.\n\nY2K black cotton tee.\n\nFlaws:\n- Small stain on hem."
    }
    apply_seller_description_facts(middle_measurements, seller)
    assert (
        middle_measurements["description"].index("Material:")
        < middle_measurements["description"].index("Measurements:")
        < middle_measurements["description"].index("Y2K black cotton tee.")
        < middle_measurements["description"].index("Flaws:")
    )

