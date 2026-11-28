"""Every visible flaw in the photos reaches the description (issue #2295).

The listing writer never sees the photos; it writes from the photo analysis.
These tests pin each step a flaw passes through: the photos are numbered, the
reading checks every photo and records each flaw with its place and count,
the analysis keeps one flaw per line, and the listing rules require all of
them in the Flaws block.
"""
import asyncio
import json

from sqlalchemy.orm import Session

from anteroom.database import Base, load_models
from anteroom.providers.chat_completions import parse_evidence
from anteroom.providers.photo_analysis import PHOTO_ANALYSIS_SYSTEM, photo_analysis_content
from anteroom.services.listing_generate import format_photo_analysis, require_photo_analysis
from worker_sessions import memory_engine, worker_sessions

# Shaped on the 2026-11-27 audit: a back stain, a mark under the neck tag,
# cracked tape inside the collar and several ink marks by the placket.
FLAWS = [
    {"description": "brown spot", "location": "upper back, left shoulder", "count": 1, "photos": [7]},
    {"description": "small spot", "location": "back, just below the neck tag", "count": 1, "photos": [6]},
    {"description": "cracking seam tape", "location": "inside neck seam", "count": 1, "photos": [5]},
    {"description": "small blue ink marks", "location": "lower front placket near the hem", "count": 3,
     "photos": [3, 4]},
]


def evidence(flaws=FLAWS, review=None):
    result = {name: {"value": "", "confidence": "low"} for name in (
        "brand", "size", "color", "material", "style", "graphic", "category", "department",
    )}
    result["brand"] = {"value": "Wrangler", "confidence": "high"}
    result.update(
        condition={"value": "Pre-owned", "visibleFlaws": flaws, "photoReview": review if review is not None else [
            {"photo": n, "view": view, "flaws": []}
            for n, view in enumerate(["front", "front close-up", "placket", "hem", "inside collar / neck tag",
                                      "back neck", "back"], start=1)
        ]},
        measurements=[], uncertainties=[],
        tag_text={"brand_label": "", "size_tag": "", "care_tag": "", "rn_number": ""},
    )
    return result


def test_photo_reading_asks_for_a_flaw_check_of_every_photo():
    prompt = PHOTO_ANALYSIS_SYSTEM.lower()
    for view in ("back", "inside", "neck tag", "placket", "cuffs", "hem"):
        assert view in prompt
    assert "check every photo" in prompt
    assert "photoreview" in prompt and "location" in prompt and "count" in prompt
    assert "never merge separate flaws" in prompt
    assert "'one spot'" in prompt


def test_photos_are_numbered_for_the_reading():
    parts = photo_analysis_content(["data:a", "data:b", "data:c"])
    labels = [part["text"] for part in parts if part["type"] == "text"]
    images = [part["image_url"]["url"] for part in parts if part["type"] == "image_url"]
    assert images == ["data:a", "data:b", "data:c"]
    assert labels[1:] == ["Photo 1:", "Photo 2:", "Photo 3:"]
    assert "3 product photos" in labels[0]
    # Each label sits right before its photo.
    assert parts[-2] == {"type": "text", "text": "Photo 3:"}


def test_every_provider_reads_numbered_photos():
    from pathlib import Path

    providers = Path(__file__).parents[1] / "anteroom" / "providers"
    readers = [path.name for path in providers.glob("*.py") if "async def analyze_photos" in path.read_text()]
    numbered = [name for name in readers if "photo_analysis_content(" in (providers / name).read_text()]
    # The sandbox and the Mac relay hand photos on without building a prompt.
    assert sorted(set(readers) - set(numbered)) == ["sandbox.py"]
    assert "Analyze these product photos:" not in "".join((providers / name).read_text() for name in readers)


def test_evidence_contract_requires_the_per_photo_review_and_located_flaws():
    assert parse_evidence(json.dumps(evidence())) == {"evidence": evidence()}
    without_review = evidence()
    del without_review["condition"]["photoReview"]
    assert parse_evidence(json.dumps(without_review)).get("error")
    undescribed = evidence(flaws=[{"location": "left cuff", "count": 1, "photos": [1]}])
    assert parse_evidence(json.dumps(undescribed)).get("error")


def test_a_flaw_without_a_place_or_count_still_reaches_the_analysis():
    # Models send null for a place or count they cannot tell; that is not a broken reading.
    vague = evidence(
        flaws=[{"description": "possible faint stain", "location": None, "count": None, "photos": None},
               {"description": "stain", "count": 1, "photos": [1]}],
        review=[{"photo": 1, "view": None, "flaws": [{"description": "stain", "location": "chest"}]},
                {"photo": 2, "flaws": None}],
    )
    parsed = parse_evidence(json.dumps(vague))
    assert "error" not in parsed
    text = format_photo_analysis(parsed["evidence"])
    assert "  - possible faint stain" in text.splitlines()
    assert "  - stain (photo 1)" in text.splitlines()


def test_review_only_flaws_survive_a_partial_summary():
    review = [{"photo": 1, "view": "front", "flaws": ["brown stain"]},
              {"photo": 2, "view": "back", "flaws": ["small hole at back hem"]}]
    flaws = [{"description": "brown stain", "location": "front left chest", "count": 1, "photos": [1]}]
    lines = format_photo_analysis(evidence(flaws=flaws, review=review)).splitlines()
    flaw_lines = [line for line in lines if line.startswith("  - ")]
    assert flaw_lines == ["  - brown stain — front left chest (photo 1)", "  - small hole at back hem (photo 2)"]


def test_a_count_in_words_is_kept():
    flaws = [{"description": "blue ink marks", "count": "several", "location": "placket", "photos": [1]},
             {"description": "several small pulls", "count": "several", "location": "sleeve", "photos": [2]}]
    text = format_photo_analysis(evidence(flaws=flaws, review=[]))
    assert "  - blue ink marks (count: several) — placket (photo 1)" in text
    assert "  - several small pulls — sleeve (photo 2)" in text


def test_analysis_lists_each_flaw_on_its_own_line_with_place_count_and_photo():
    text = format_photo_analysis(evidence())
    lines = text.splitlines()
    flaw_lines = [line for line in lines if line.startswith("  - ")]
    assert flaw_lines == [
        "  - brown spot — upper back, left shoulder (photo 7)",
        "  - small spot — back, just below the neck tag (photo 6)",
        "  - cracking seam tape — inside neck seam (photo 5)",
        "  - 3 × small blue ink marks — lower front placket near the hem (photos 3, 4)",
    ]
    assert any("disclose every one" in line for line in lines)
    assert "- photos checked for flaws: 1 front; 2 front close-up; 3 placket" in text
    assert "7 back" in text


def test_flaws_the_photo_pass_saw_survive_an_empty_summary():
    review = [{"photo": 1, "view": "front", "flaws": []},
              {"photo": 2, "view": "back", "flaws": ["faded fabric across the back", "frayed hem"]}]
    text = format_photo_analysis(evidence(flaws=[], review=review))
    assert "  - faded fabric across the back (photo 2)" in text
    assert "  - frayed hem (photo 2)" in text


def test_a_plain_text_flaw_still_gets_its_own_line():
    # Mac apps on an older build return the earlier shape to the hosted API.
    text = format_photo_analysis(evidence(flaws=["stain on left chest", "fray at pocket flap"], review=[]))
    assert "  - stain on left chest" in text and "  - fray at pocket flap" in text
    assert "photos checked" not in text


def test_listing_writer_gets_every_flaw_and_the_rule_to_disclose_them_all(monkeypatch):
    from anteroom.providers.sandbox import SandboxProvider
    from anteroom.services import chat_prompts

    monkeypatch.setattr("anteroom.services.marketplaces.get_selected_marketplaces", lambda: [])
    monkeypatch.setattr(SandboxProvider, "analyze_photos",
                        lambda self, *_args, **_kwargs: asyncio.sleep(0, {"evidence": evidence()}))
    result = asyncio.run(SandboxProvider().analyze_photos(["photo.jpg"]))
    _evidence, analysis = require_photo_analysis(result)

    load_models()
    engine = memory_engine()
    Base.metadata.create_all(engine)
    with worker_sessions(engine), Session(engine) as db:
        messages = chat_prompts.listing_generation_messages(
            chat_prompts.load_skill_rules(analysis), "", analysis, db, "flaw-item", photo_count=7)
    engine.dispose()
    system = messages[0]["content"]
    for flaw in FLAWS:
        assert flaw["location"] in system
    assert "3 × small blue ink marks" in system
    # The canonical description rule, from skills/list-this/SKILL.md.
    assert "Disclose every flaw the photo analysis lists" in system
    assert 'never "one faint spot"' in system
