"""The photo-reading prompt every listing provider sends with the seller's photos."""

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
    '- condition: {value, visibleFlaws: []}\n'
    '- measurements: [{label, value, source}]\n'
    '- category: {value, confidence}\n'
    '- department: {value, confidence} — who the item is cut for: Women, Men, Girls, Boys, Baby, or Unisex. Category trees split on this first, so infer it from cut, styling and sizing when no label says it.\n'
    '- uncertainties: [{field, issue}]\n'
    '- tag_text: {brand_label, size_tag, care_tag, rn_number} copied verbatim from any visible labels, empty strings when not visible\n'
    '\n'
    'Keep every required field, using empty values and uncertainties for unknowns. Never rename keys.\n'
    'Transcribe tag_text verbatim, including apparent spelling mistakes; do not correct or expand it.\n'
    'For every populated product field, explain its visible support in evidence. Label any inference explicitly. '
    'Do not infer fiber composition from texture, size from a brand tab or product code, age from styling, '
    'or overall condition from a label/detail photo. Use unknown when the views do not establish condition. '
    'Do not call an item new merely because it looks clean. Record observed damage without guessing whether it was intentional.\n'
    'Be conservative. Flag uncertainty. Do not invent details.'
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
        "condition": {"type": "object", "required": ["value", "visibleFlaws"], "properties": {
            "value": {"type": ["string", "null"]},
            "visibleFlaws": {"type": "array", "items": {"type": "string"}},
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
    },
}
