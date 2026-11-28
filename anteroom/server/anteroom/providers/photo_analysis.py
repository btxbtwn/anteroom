"""The photo-reading prompt every listing provider sends with the seller's photos."""

# Generation writes the description from this reading alone: the listing
# writer never sees the photos. A flaw this pass skips, merges or leaves
# without a place is a flaw the buyer is never told about.
FLAW_REVIEW_RULES = (
    'Flaw review: the photos are numbered in the order given (Photo 1 is the first). '
    'Check every photo on its own for damage before you summarize condition, including back views, '
    'inside and lining shots, collar and neck tag photos, care and size tag close-ups, the placket and '
    'buttons, cuffs, pockets, seams and hem. Damage seen only in a tag, inside or back photo is still a flaw.\n'
    '- photoReview: one entry per photo, in order: {photo: its number, view: what it shows '
    "(e.g. 'back', 'inside collar / neck tag'), flaws: every mark or damage visible in it, empty when none}.\n"
    '- visibleFlaws: one entry per distinct flaw: {description: what it is (stain, ink mark, bleach spot, '
    "hole, fray, fading, pilling, cracked seam tape...), location: where on the item (e.g. 'upper back, left "
    "shoulder', 'front placket beside the 2nd button', 'inside neck seam tape'), count: how many marks, "
    'photos: the photo numbers that show it}.\n'
    'Never merge separate flaws in different places into one entry. When several marks of the same kind '
    "sit together, keep one entry and give the real count (count 3, 'several small blue ink marks'), never "
    "'one spot'. A faint or uncertain mark is still listed, described as possible. "
    'Lint and wrinkles are not flaws.'
)

PHOTO_ANALYSIS_SYSTEM = (
    'You are a product listing analyst. Examine these product photos and extract structured evidence about the item. Separate visible facts from inference.\n'
    '\n'
    'Return ONLY valid JSON with these fields:\n'
    '- brand: {value, confidence, evidence}\n'
    "- size: {value, source: 'tag'|'measurement-derived'|'unreadable', confidence}\n"
    '- color: {value, confidence}\n'
    '- material: {value, confidence}\n'
    '- style: {value, confidence}\n'
    "- graphic: {value, confidence} — the named character, franchise, band, team, show or logo printed on the item (e.g. 'Snoopy and Woodstock'), empty when there is none. Generic prints belong in style, not here.\n"
    '- condition: {value, photoReview: [{photo, view, flaws: []}], visibleFlaws: [{description, location, count, photos: []}]}\n'
    '- measurements: [{label, value, source}]\n'
    '- category: {value, confidence}\n'
    '- department: {value, confidence} — who the item is cut for: Women, Men, Girls, Boys, Baby, or Unisex. Category trees split on this first, so infer it from cut, styling and sizing when no label says it.\n'
    '- uncertainties: [{field, issue}]\n'
    '- tag_text: {brand_label, size_tag, care_tag, rn_number} copied verbatim from any visible labels, empty strings when not visible\n'
    '- slab_label: optional {grader, grade, certification_number} copied from a readable professional card or coin slab. '
    'Preserve leading zeros in certification numbers and include the visible coin letter grade (for example MS-65). '
    'Leave fields blank when unreadable.\n'
    '\n'
    'Keep every required field, using empty values and uncertainties for unknowns. Never rename keys.\n'
    'Transcribe tag_text verbatim, including apparent spelling mistakes; do not correct or expand it.\n'
    'For every populated product field, explain its visible support in evidence. Label any inference explicitly. '
    'Do not infer fiber composition from texture, size from a brand tab or product code, age from styling, '
    'or overall condition from a label/detail photo. Use unknown when the views do not establish condition. '
    'Do not call an item new merely because it looks clean. Record observed damage without guessing whether it was intentional.\n'
    'Be conservative about identity facts (brand, size, material, era). Flag uncertainty. Do not invent details.\n'
    '\n'
    + FLAW_REVIEW_RULES
)


# Validate the shared photo prompt before any of its facts enter a listing.
# JSON syntax alone does not protect tag text or uncertainty from misspelled keys.
PHOTO_EVIDENCE_SCHEMA = {
    "type": "object",
    "required": ["brand", "size", "color", "material", "style", "graphic", "condition",
                 "measurements", "category", "department", "uncertainties", "tag_text"],
    "additionalProperties": False,
    "properties": {
        **{name: {
            "type": "object", "required": ["value", "confidence"],
            "properties": {"value": {"type": ["string", "null"]},
                           "confidence": {"type": ["string", "number"]}},
        } for name in ("brand", "size", "color", "material", "style", "graphic", "category", "department")},
        "condition": {"type": "object", "required": ["value", "photoReview", "visibleFlaws"], "properties": {
            "value": {"type": ["string", "null"]},
            # Only what a flaw cannot do without is required. Models send null for a
            # place or count they cannot tell (the prompt asks for faint marks too),
            # and the formatter reads whatever else arrives.
            "photoReview": {"type": "array", "items": {
                "type": "object", "required": ["photo"],
                "properties": {"photo": {"type": ["integer", "string"]}, "view": {"type": ["string", "null"]},
                               "flaws": {"type": ["array", "null"], "items": {"type": ["string", "object"]}}},
            }},
            "visibleFlaws": {"type": "array", "items": {
                "type": "object", "required": ["description"],
                "properties": {"description": {"type": "string"}, "location": {"type": ["string", "null"]},
                               "count": {"type": ["integer", "string", "null"]},
                               "photos": {"type": ["array", "null"], "items": {"type": ["integer", "string"]}}},
            }},
        }},
        "measurements": {"type": "array", "items": {
            "type": "object", "required": ["label", "value", "source"],
            "properties": {name: {"type": "string"} for name in ("label", "value", "source")},
        }},
        "uncertainties": {"type": "array", "items": {
            "type": "object", "required": ["field", "issue"],
            "properties": {name: {"type": "string"} for name in ("field", "issue")},
        }},
        "tag_text": {"type": "object", "required": ["brand_label", "size_tag", "care_tag", "rn_number"],
                     "properties": {name: {"type": "string"} for name in
                                    ("brand_label", "size_tag", "care_tag", "rn_number")}},
        "slab_label": {"type": "object", "additionalProperties": False,
                       "properties": {name: {"type": "string"} for name in ("grader", "grade", "certification_number")}},
    },
}


def photo_analysis_content(image_urls: list[str]) -> list[dict]:
    """The user turn of a photo reading: every photo numbered, so flaws can name the photo that shows them."""
    parts: list[dict] = [{"type": "text", "text": (
        f"Analyze these {len(image_urls)} product photos. They are numbered in order; "
        "review every one for flaws, including back, inside and tag photos."
    )}]
    for number, url in enumerate(image_urls, start=1):
        parts.append({"type": "text", "text": f"Photo {number}:"})
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts
