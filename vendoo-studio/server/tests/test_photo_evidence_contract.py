import json

import pytest

from vendoo_studio.providers.chat_completions import parse_evidence


def evidence():
    result = {name: {"value": "", "confidence": "low"} for name in (
        "brand", "size", "color", "material", "style", "graphic", "category", "department",
    )}
    result["size"]["source"] = "unreadable"
    result.update(condition={"value": "unknown", "visibleFlaws": []}, measurements=[], uncertainties=[],
                  tag_text={"brand_label": "", "size_tag": "", "care_tag": "", "rn_number": ""})
    return result


@pytest.mark.parametrize("field", ["tag_text", "uncertainties"])
def test_rejects_misspelled_photo_fields(field):
    reply = evidence()
    reply["misspelled_" + field] = reply.pop(field)
    result = parse_evidence(json.dumps(reply))
    assert result.get("error")
    assert result["evidence"] == {}


@pytest.mark.parametrize("field,value", [("style", "soft cotton"), ("measurements", "36 inches"),
                                         ("condition", {"value": "good", "visibleFlaws": "none"})])
def test_rejects_invalid_photo_field_types(field, value):
    reply = evidence()
    reply[field] = value
    assert parse_evidence(json.dumps(reply)).get("error")


def test_accepts_complete_unknowns_without_filling_them():
    reply = evidence()
    assert parse_evidence(json.dumps(reply)) == {"evidence": reply}
