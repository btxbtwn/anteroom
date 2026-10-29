from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.registry import RegistryService

log = logging.getLogger("vendoo_studio.listing_generate")

PHOTO_ANALYSIS_PREFIXES = ("Photo analysis:", "Photo analysis of the uploaded product images:")
PHOTO_ANALYSIS_RETRY_MESSAGE = "Photo analysis failed. Retry to analyze the photos again."
_FIELD_LINE_RE = re.compile(r"^-\s*\w[\w\s]*:", re.M)


class PhotoAnalysisError(RuntimeError):
    pass


def photo_analysis_failure(reason: object) -> PhotoAnalysisError:
    """The retry error, naming why the photos could not be read.

    A bare "failed" left a seller whose key or plan was the problem with
    nothing to fix, so the provider's own reason goes into the message.
    """
    if isinstance(reason, BaseException):
        reason = str(reason).strip() or type(reason).__name__
    detail = " ".join(str(reason or "").split())[:240].strip()
    if not detail:
        return PhotoAnalysisError(PHOTO_ANALYSIS_RETRY_MESSAGE)
    log.warning("photo analysis failed: %s", detail)
    stop = "" if detail.endswith((".", "!", "?", '"')) else "."
    return PhotoAnalysisError(f"Photo analysis failed: {detail}{stop} Retry to analyze the photos again.")


def _evidence_value(field: Any) -> str | None:
    if isinstance(field, dict):
        raw = field.get("value")
    else:
        raw = field
    text = str(raw or "").strip()
    return text or None


def normalize_evidence(payload: Any) -> dict:
    """Unwrap common vision JSON shapes into a flat evidence dict."""
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("evidence")
    if isinstance(nested, dict) and any(
        key in nested
        for key in ("brand", "size", "color", "material", "style", "category", "department", "condition")
    ):
        return nested
    return payload


def photo_analysis_usable(text: str | None) -> bool:
    """True when analysis has at least one real field line (not just the header)."""
    stripped = (text or "").strip()
    if not stripped.startswith(PHOTO_ANALYSIS_PREFIXES):
        return False
    return bool(_FIELD_LINE_RE.search(stripped))


def photo_fingerprint(photos: list[Any]) -> str:
    """Bind a reading to the ordered photo contents, including rendered edits."""
    payload = [(p.id, p.stored_filename, p.checksum) for p in photos]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def current_photo_analysis(repo: ConversationRepo, conv_id: str) -> str | None:
    return latest_photo_analysis(repo.get_messages(conv_id), fingerprint=photo_fingerprint(repo.get_photos(conv_id)))


def latest_photo_analysis(messages: list[Any], *, fingerprint: str | None = None) -> str | None:
    for msg in reversed(messages):
        text = (getattr(msg, "text", None) or "").strip()
        if (getattr(msg, "role", None) == "system" and photo_analysis_usable(text)
                and (fingerprint is None or getattr(msg, "photo_fingerprint", None) == fingerprint)):
            return text
    return None


def format_photo_analysis(evidence: dict) -> str:
    evidence = normalize_evidence(evidence)
    parts = ["Photo analysis:"]
    # department before category: the trees split on it, and a reader — human or
    # model — should see who the item is for before what it is.
    for field_key in (
        "brand", "size", "color", "material", "style", "graphic", "department", "category", "condition",
    ):
        fd = evidence.get(field_key)
        value = _evidence_value(fd)
        if not value:
            continue
        extra = ""
        if isinstance(fd, dict) and fd.get("source"):
            extra = f" (source: {fd.get('source')})"
        parts.append(f"- {field_key}: {value}{extra}")
        if isinstance(fd, dict):
            detail = []
            if fd.get("evidence"):
                detail.append(f"evidence: {fd['evidence']}")
            if fd.get("evidencePhotoIds"):
                detail.append(f"photo ids: {fd['evidencePhotoIds']}")
            if fd.get("confidence") is not None:
                detail.append(f"model confidence (not verified): {fd['confidence']}")
            if detail:
                parts.append(f"Evidence for {field_key}: {'; '.join(detail)}")
    condition = evidence.get("condition")
    flaws = condition.get("visibleFlaws", []) if isinstance(condition, dict) else []
    if flaws:
        parts.append(f"- flaws: {', '.join(str(f) for f in flaws if f)}")
    for measurement in evidence.get("measurements") or []:
        if isinstance(measurement, dict):
            label = str(measurement.get("label") or "").strip()
            value = str(measurement.get("value") or "").strip()
            if label and value:
                source = f" (source: {measurement['source']})" if measurement.get("source") else ""
                parts.append(f"- {label}: {value}{source}")
    tag_text = evidence.get("tag_text")
    if isinstance(tag_text, dict):
        tag_parts = [
            f'{label} "{str(tag_text.get(key) or "").strip()}"'
            for key, label in (
                ("brand_label", "brand label"),
                ("size_tag", "size tag"),
                ("care_tag", "care tag"),
                ("rn_number", "RN"),
            )
            if str(tag_text.get(key) or "").strip()
        ]
        if tag_parts:
            parts.append("- tag text: " + "; ".join(tag_parts))
    uncertainties = evidence.get("uncertainties") or []
    if uncertainties:
        parts.append("- uncertainties: " + ", ".join(
            f"{u.get('field', 'item')}: {u.get('issue', 'uncertain')}"
            if isinstance(u, dict) else str(u) for u in uncertainties
        ))
    return "\n".join(parts)


def require_photo_analysis(result: Any) -> tuple[dict, str]:
    """Return usable photo evidence or stop the listing flow for a retry."""
    if not isinstance(result, dict):
        raise photo_analysis_failure("the AI returned no answer")
    if result.get("error"):
        raise photo_analysis_failure(result["error"])
    evidence = normalize_evidence(result.get("evidence", {}) or {})
    analysis_text = format_photo_analysis(evidence)
    if not photo_analysis_usable(analysis_text):
        # The reply is the only clue to why: a model that cannot see images
        # says so in prose, which leaves nothing to parse.
        reply = " ".join(str(result.get("raw") or "").split())
        if reply:
            log.warning("photo analysis reply had no details: %s", reply[:4000])
            raise photo_analysis_failure(f'the AI did not describe the photos. It replied: "{reply[:150]}"')
        log.warning("photo analysis returned no details: %s", json.dumps(evidence, default=str)[:4000])
        raise photo_analysis_failure("the AI read no details from the photos")
    return evidence, analysis_text


TAG_FIELDS = ("brand", "size", "material")
_UNREADABLE_VALUES = {"unreadable", "illegible", "unknown", "not visible", "not legible"}
_UNREADABLE_ISSUE_RE = re.compile(
    r"unreadable|illegible|not legible|blurr|too small|can(?:no|')t read|hard to read|low resolution",
    re.I,
)


def unreadable_tag_fields(evidence: dict) -> list[str]:
    """Tag-derived fields the vision pass could not read (blank brand alone is not a signal)."""
    evidence = normalize_evidence(evidence)
    flagged: list[str] = []
    for field_key in TAG_FIELDS:
        fd = evidence.get(field_key)
        value = (_evidence_value(fd) or "").casefold()
        source = str(fd.get("source") or "").casefold() if isinstance(fd, dict) else ""
        if value in _UNREADABLE_VALUES or source == "unreadable":
            flagged.append(field_key)
    for item in evidence.get("uncertainties") or []:
        if not isinstance(item, dict):
            continue
        field_key = str(item.get("field") or "").strip().casefold()
        if field_key in TAG_FIELDS and field_key not in flagged and _UNREADABLE_ISSUE_RE.search(
            str(item.get("issue") or "")
        ):
            flagged.append(field_key)
    return flagged


async def analyze_photos_with_tag_retry(provider, paths: list[str], **kwargs) -> Any:
    """Analyze downscaled photos; resend originals only when tag text was unreadable."""
    from vendoo_studio.providers.xiaomi_mimo import VISION_MAX_SIDE, image_exceeds

    result = await provider.analyze_photos(paths, **kwargs)
    if not isinstance(result, dict) or result.get("error"):
        return result
    evidence = normalize_evidence(result.get("evidence") or {})
    flagged = unreadable_tag_fields(evidence)
    if not flagged or not any(image_exceeds(path, VISION_MAX_SIDE) for path in paths):
        return result

    log.info("photo analysis could not read %s; retrying at full resolution", ", ".join(flagged))
    try:
        retry = await provider.analyze_photos(paths, max_side=None, **kwargs)
    except Exception:
        log.exception("full-resolution photo analysis retry failed")
        return result
    if not isinstance(retry, dict) or retry.get("error"):
        return result
    retry_evidence = normalize_evidence(retry.get("evidence") or {})
    still_flagged = set(unreadable_tag_fields(retry_evidence))
    resolved = [
        key for key in flagged
        if key not in still_flagged and _evidence_value(retry_evidence.get(key))
    ]
    if not resolved:
        return result
    merged = {**evidence, **{key: retry_evidence[key] for key in resolved}}
    merged["uncertainties"] = [
        item for item in (evidence.get("uncertainties") or [])
        if not (isinstance(item, dict) and str(item.get("field") or "").casefold() in resolved)
    ]
    return {**result, "evidence": merged}


IDENTITY_FIELDS = ("brand", "graphic", "style", "category", "department", "size", "color")
IDENTITY_PHOTOS = 3
IDENTITY_MAX_SIDE = 768
IDENTITY_TIMEOUT_SEC = 45
_IDENTITY_SYSTEM = (
    "Identify this secondhand item for a marketplace price search. Return ONLY JSON "
    '{"brand": "", "graphic": "", "style": "", "category": "", "department": "", '
    '"size": "", "color": ""}. Short values, empty when not visible. '
    "brand: the maker on the label or logo — never guess one. "
    "graphic: a named character, franchise, band, team or logo printed on it. "
    "style: the item style in at most three words. category: the item type, e.g. T-Shirt. "
    "department: Women, Men, Girls, Boys, Baby or Unisex. size: from the tag only."
)


async def quick_identity_read(provider, paths: list[str], notes: str = "") -> dict[str, str]:
    """Brand, item type and size from a few small photos — enough to search comps.

    The full photo analysis reads every photo at full detail; comps only need
    to know what the item is, so this runs beside it and lets comps start
    early. Empty when the provider has no vision chat or the read fails.
    """
    from vendoo_studio.providers.xiaomi_mimo import encode_images, unpack_stream_item

    vision_chat = getattr(provider, "vision_chat", None)
    if vision_chat is None or not paths:
        return {}
    parts: list[dict] = [{"type": "text", "text": "Identify this item."}]
    if notes:
        parts[0]["text"] += f"\nSeller notes: {notes[:1000]}"
    for url in await encode_images(paths[:IDENTITY_PHOTOS], IDENTITY_MAX_SIDE):
        parts.append({"type": "image_url", "image_url": {"url": url}})
    messages = [{"role": "system", "content": _IDENTITY_SYSTEM}, {"role": "user", "content": parts}]
    text = ""
    async with asyncio.timeout(IDENTITY_TIMEOUT_SEC):
        async for item in vision_chat(messages):
            kind, piece = unpack_stream_item(item)
            if kind == "content" and piece:
                text += piece
    match = re.search(r"\{[\s\S]*\}", text)
    try:
        payload = json.loads(match.group()) if match else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key in IDENTITY_FIELDS
        if (value := " ".join(str(payload.get(key) or "").split()))
    }


def analysis_with_photo_count(photo_count: int, analysis_text: str | None = None) -> str:
    """Attach verified photo evidence to a listing prompt."""
    count = max(0, int(photo_count or 0))
    text = (analysis_text or "").strip()
    if not count:
        return text
    if not photo_analysis_usable(text):
        raise PhotoAnalysisError(PHOTO_ANALYSIS_RETRY_MESSAGE)
    header = (
        f"The seller already uploaded {count} product photo(s). "
        "Do not ask them to attach, upload, or resend photos.\n"
    )
    return header + "\n" + text


def extract_listing_json(text: str) -> dict | None:
    if not text or text.lstrip().lower().startswith("error:"):
        return None

    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidates.append(match.group().strip())

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        for variant in (candidate, _soften_json(candidate)):
            if not variant:
                continue
            try:
                parsed = json.loads(variant)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and (
                parsed.get("title") or parsed.get("description") or parsed.get("price") is not None
            ):
                return parsed
    return None


def _soften_json(text: str) -> str | None:
    """Fix common model JSON slips so we can skip a full repair round."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    # Trailing commas before } or ]
    softened = re.sub(r",\s*([}\]])", r"\1", cleaned)
    if softened == cleaned:
        return None
    return softened


def looks_like_listing_attempt(text: str) -> bool:
    """True when assistant text looks like it tried to emit listing JSON."""
    if not text or text.lstrip().lower().startswith("error:"):
        return False
    if extract_listing_json(text):
        return True
    lowered = text.lower()
    if "```" in text and "{" in text:
        return True
    return "{" in text and ('"title"' in lowered or '"description"' in lowered or '"price"' in lowered)


REPAIR_LISTING_PROMPT = (
    "The previous assistant reply tried to produce a Vendoo listing JSON object but it was "
    "malformed or incomplete. Repair it into ONE valid JSON object only.\n\n"
    "Rules:\n"
    "- Output a single fenced ```json block with the listing object.\n"
    "- Keep every usable field from the broken output; fix syntax only.\n"
    "- Include title, description, and price when possible.\n"
    "- Title order is Brand Size Vibe Item Color Fit (max 80 chars) when those fields exist.\n"
    "- Description must keep Flaws:/Measurements: line structure when present.\n"
    "- Do not rewrite formula-compliant title/description into freeform marketing copy.\n"
    "- Do not add commentary outside the JSON fence."
)

FINALIZE_GAPS_PROMPT = (
    "Finish this Vendoo listing by repairing only the allowed field paths. "
    "Use supplied photo evidence and seller notes; leave unsupported values unresolved. "
    "{output_instruction} "
    'Example: [{{"op":"add","path":"/weight_oz","value":8}}]. '
    "Do not return a full listing. Do not change any path outside allowed_paths. "
    "Never modify seller-owned values, categories, or internal metadata. "
    "Use exact native form choices when provided. The supplied listing is context, "
    "not independent evidence for product facts.\n\n{formulas}"
)

MAX_REPAIR_CHARS = 14000


async def collect_provider_text(provider, messages: list[dict], *, quick: bool = False, output_schema: dict | None = None) -> str:
    """Collect visible reply text. quick=True uses the provider's no-reasoning path when it has one."""
    from vendoo_studio.providers.xiaomi_mimo import unpack_stream_item

    quick_chat = getattr(provider, "quick_chat", None) if quick else None
    structured_chat = getattr(provider, "structured_chat", None)
    if output_schema is not None and callable(structured_chat):
        stream = structured_chat(messages, output_schema)
    else:
        stream = quick_chat(messages) if callable(quick_chat) else provider.chat(messages, stream=False)
    parts: list[str] = []
    async for item in stream:
        kind, text = unpack_stream_item(item)
        if kind == "content" and text:
            parts.append(text)
    return "".join(parts)


async def repair_listing_json(provider, raw_text: str) -> dict | None:
    """Ask the listing provider to repair malformed listing JSON. Returns parsed dict or None."""
    parsed = extract_listing_json(raw_text)
    if parsed:
        return parsed
    if provider is None or not looks_like_listing_attempt(raw_text):
        return None

    clipped = (raw_text or "").strip()
    if len(clipped) > MAX_REPAIR_CHARS:
        clipped = clipped[:MAX_REPAIR_CHARS]
    messages = [
        {"role": "system", "content": REPAIR_LISTING_PROMPT},
        {"role": "user", "content": clipped},
    ]
    try:
        repaired = await collect_provider_text(provider, messages, quick=True)
    except Exception:
        log.exception("listing JSON repair request failed")
        return None
    return extract_listing_json(repaired)


def _first_sentence(text: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return fallback
    match = re.search(r"(.+?[.!?])(?:\s|$)", cleaned)
    sentence = (match.group(1) if match else cleaned).strip()
    if not sentence.endswith((".", "!", "?")):
        sentence += "."
    return sentence


# Pricing talk belongs on the price field, never in buyer-facing copy. The model
# otherwise leaks comp research ("limited sold comps so price is approximate")
# into the vibe sentence when comps are thin.
_PRICING_TERMS_RE = re.compile(
    r"(?i)(?:\$\s*\d|\bprices?d?\b|\bpricing\b|\bcomps?\b|\bcomparables?\b|\bsold listings?\b"
    r"|\bmarket value\b|\bmsrp\b|\bretail(?:s|ed)? for\b|\bresale value\b|\bworth\b|\bvalued?\b"
    r"|\boffers?\b|\bnegotiable\b|\bbest offer\b|\bdiscount(?:s|ed)?\b|\bfirm\b)"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


# Uncertainty is internal: the seller hears it in the chat reply, a buyer never
# reads "photo estimates" or "color is uncertain" in the listing.
_UNCERTAINTY_TERMS_RE = re.compile(
    r"(?i)(?:\buncertain(?:ty|ties)?\b|\bunclear\b|\bunsure\b|\bunverified\b|\bunconfirmed\b"
    r"|\bunreadable\b|\billegible\b|\bestimat(?:e|es|ed|ion)\b|\bapprox(?:\.|imate(?:ly)?\b)"
    r"|\bnot (?:sure|certain|confirmed|verified)\b"
    r"|\b(?:could(?:n't| not)|can(?:'t|not)) (?:be )?(?:verif|confirm|read|determin|tell)"
    r"|\bno (?:readable |visible )?(?:size |brand |care )?tag\b|\bmissing (?:size |brand )?tag\b)"
)
_PARENTHETICAL_RE = re.compile(r"\s*\([^()]*\)")


def _strip_description_sentences(listing: dict, pattern: re.Pattern[str]) -> bool:
    """Drop description sentences (and asides) matching ``pattern``, keeping the formula blocks."""
    if not isinstance(listing, dict):
        return False
    desc = str(listing.get("description") or "")
    if not desc.strip() or not pattern.search(desc):
        return False

    def keep(part: str) -> bool:
        return bool(part.strip()) and not pattern.search(part)

    kept_blocks: list[str] = []
    for block in re.split(r"\n\s*\n", desc):
        block = _PARENTHETICAL_RE.sub(
            lambda match: "" if pattern.search(match.group(0)) else match.group(0), block
        )
        raw = block.strip()
        lines = raw.splitlines()
        # A flaw list is one item per line. Joining those lines into a paragraph
        # would turn the list back into the run-on it replaced.
        if any(re.match(r"\s*[-*•]\s+", line) for line in lines):
            kept_lines = [line.strip() for index, line in enumerate(lines) if line.strip() and (index == 0 or keep(line))]
            if kept_lines:
                kept_blocks.append("\n".join(kept_lines))
            continue
        sentences = _SENTENCE_SPLIT_RE.split(raw)
        # Flaws:/Measurements: blocks are structural; their labelled first
        # sentence stays, only trailing asides can go.
        if raw.lower().startswith(("flaws:", "measurements:")):
            kept = [sentences[0], *filter(keep, sentences[1:])]
        else:
            kept = [part for part in sentences if keep(part)]
        if kept:
            kept_blocks.append(" ".join(part.strip() for part in kept))
    # Stripping can eat the whole vibe sentence; fall back to the title so the
    # description keeps its opening line.
    if kept_blocks and kept_blocks[0].strip().lower().startswith(("flaws:", "measurements:")):
        title = str(listing.get("title") or "").strip()
        kept_blocks.insert(0, f"{title}." if title else "Resale-ready item.")
    cleaned = "\n\n".join(block for block in kept_blocks if block.strip()).strip()
    if cleaned == desc.strip():
        return False
    listing["description"] = cleaned
    return True


def strip_pricing_from_description(listing: dict) -> bool:
    """Remove any pricing/comp talk from the buyer-facing description."""
    return _strip_description_sentences(listing, _PRICING_TERMS_RE)


def strip_uncertainty_from_description(listing: dict) -> bool:
    """Remove hedges about unverified details from the buyer-facing description."""
    return _strip_description_sentences(listing, _UNCERTAINTY_TERMS_RE)


def clear_not_applicable_values(listing: dict) -> bool:
    """Empty every "Does Not Apply" a model wrote and remember the field as reviewed.

    The listing never stores the phrase. An empty field is the answer for an
    attribute that does not apply; ``reviewed_empty_fields`` keeps gap checks
    from asking about it again.
    """
    from vendoo_studio.services.fill_log import remember_chat_clears
    from vendoo_studio.services.vendoo_specifics import is_not_applicable

    if not isinstance(listing, dict):
        return False
    cleared: list[dict] = []

    def clear(record: dict, marketplace: str) -> None:
        for key, value in list(record.items()):
            if isinstance(value, dict) and key == "category_specifics":
                clear(value, marketplace)
            elif is_not_applicable(value):
                record.pop(key)
                cleared.append({"marketplace": marketplace, "field": key, "value": ""})

    clear(listing, "general")
    for key, block in listing.items():
        if key.endswith("_specifics") and isinstance(block, dict):
            clear(block, key.removesuffix("_specifics"))
    if not cleared:
        return False
    listing.update(remember_chat_clears(listing, cleared))
    return True


def strip_empty_flaws_from_description(listing: dict) -> bool:
    """Drop a Flaws block that only says there are none; a flawless item has no Flaws line."""
    from vendoo_studio.services.listing_carryover import flaw_items

    if not isinstance(listing, dict):
        return False
    desc = str(listing.get("description") or "")
    blocks = re.split(r"\n\s*\n", desc.strip())
    kept = [
        block
        for block in blocks
        if not re.match(r"(?i)\s*flaws?\s*:", block) or flaw_items(block)
    ]
    if len(kept) == len(blocks):
        return False
    listing["description"] = "\n\n".join(kept)
    return True


def ensure_physical_description(listing: dict) -> bool:
    """Rewrite description into the trendy-keyword/Measurements formula when markers are missing."""
    from vendoo_studio.models.validation import _description_follows_formula
    from vendoo_studio.models.etsy_fields import is_etsy_digital_listing

    if not isinstance(listing, dict):
        return False
    if is_etsy_digital_listing(listing):
        return False
    desc = str(listing.get("description") or "").strip()
    if not desc or _description_follows_formula(desc):
        return False

    meas = "See photos"
    meas_match = re.search(r"(?is)measurements?:\s*(.+?)(?:\n\n|\n[A-Z]|$)", desc)
    if meas_match:
        meas = re.sub(r"\s+", " ", meas_match.group(1)).strip().rstrip(".")
    else:
        bits = re.findall(
            r"(?i)(?:pit\s*to\s*pit|length|sleeve|waist|rise|inseam|leg\s*opening)\s*[:=]?\s*[\d.\/\"]+\s*(?:inches|in|\"|”)?",
            desc,
        )
        if bits:
            meas = "; ".join(re.sub(r"\s+", " ", bit).strip() for bit in bits)

    # Prefer appending the missing Measurements block so existing vibe prose stays intact.
    if "\n" in desc and len(desc) >= 40 and "measurements:" not in desc.lower():
        listing["description"] = f"{desc.rstrip()}\n\nMeasurements: {meas}"
        return True

    title = str(listing.get("title") or "").strip()
    vibe = _first_sentence(desc, fallback=f"{title}." if title else "Resale-ready item.")
    flaws = re.search(r"(?im)^flaws?:.*$", desc)
    blocks = [vibe, flaws.group(0).strip() if flaws else "", f"Measurements: {meas}"]
    listing["description"] = "\n\n".join(block for block in blocks if block)
    return True


_SIZE_APPROX_PREFIX_RE = re.compile(
    r"^(?:approx(?:imately)?\.?|about|around|est\.?|estimated|~)\s*:?\s*",
    re.I,
)
_SIZE_SPECIFIC_KEYS = (
    "ebay_specifics",
    "poshmark_specifics",
    "mercari_specifics",
    "depop_specifics",
    "etsy_specifics",
)


def sanitize_size_value(raw: Any) -> str:
    """Strip estimate qualifiers so size is a marketplace dropdown value."""
    text = str(raw or "").strip()
    if not text:
        return ""
    cleaned = _SIZE_APPROX_PREFIX_RE.sub("", text).strip()
    return cleaned or text


def sanitize_listing_sizes(listing: dict) -> bool:
    """Normalize size fields to clean marketplace values (no 'approx 10')."""
    if not isinstance(listing, dict):
        return False
    changed = False
    size = sanitize_size_value(listing.get("size"))
    if size and size != str(listing.get("size") or "").strip():
        listing["size"] = size
        changed = True
    for key in ("size_us", "sizeUs"):
        if key not in listing:
            continue
        cleaned = sanitize_size_value(listing.get(key))
        if cleaned and cleaned != str(listing.get(key) or "").strip():
            listing[key] = cleaned
            changed = True
    for specifics_key in _SIZE_SPECIFIC_KEYS:
        block = listing.get(specifics_key)
        if not isinstance(block, dict) or "size" not in block:
            continue
        cleaned = sanitize_size_value(block.get("size"))
        if cleaned and cleaned != str(block.get("size") or "").strip():
            listing[specifics_key] = {**block, "size": cleaned}
            changed = True
    return changed


# Vendoo size dropdowns are the waist (`34`). `34x34` does not match, so the field stays blank.
_BOTTOMS_RE = re.compile(
    r"(?i)\b(jeans|pants|trousers|chinos|slacks|shorts|joggers|sweatpants|cargos|corduroys)\b"
)
_WAIST_ONLY_RE = re.compile(r"^(\d{2})\s*(?:w|in|inch|inches|\"|”)?$", re.I)
_WAIST_INSEAM_RE = re.compile(r"(?i)(?<![\dx])(\d{2})\s*[x\u00d7/]\s*(\d{2})(?![\dx])")
_INSEAM_LABEL_RE = re.compile(r"(?i)\binseam\b\s*[:=]?\s*(\d{1,2})(?:\.5)?")


def _is_mens_bottoms(listing: dict) -> bool:
    ebay = listing.get("ebay_specifics")
    ebay = ebay if isinstance(ebay, dict) else {}
    # "women" also contains "men", so only a leading "men" counts as menswear.
    department = str(listing.get("department") or ebay.get("department") or "").strip().lower()
    category = str(listing.get("category_path") or "").strip().lower()
    if not department.startswith("men") and not category.startswith("men"):
        return False
    haystack = " ".join(
        str(value or "")
        for value in (
            listing.get("category_path"),
            listing.get("title"),
            ebay.get("type"),
        )
    )
    return bool(_BOTTOMS_RE.search(haystack))


def _bottoms_inseam(listing: dict, waist: str) -> str:
    """Inseam for a waist-only men's size, from the title, eBay specifics, or measurements."""
    title_match = _WAIST_INSEAM_RE.search(str(listing.get("title") or ""))
    if title_match and title_match.group(1) == waist:
        return title_match.group(2)
    ebay = listing.get("ebay_specifics")
    if isinstance(ebay, dict):
        inseam = re.match(r"^\s*(\d{1,2})", str(ebay.get("inseam") or ""))
        if inseam:
            return inseam.group(1)
    label = _INSEAM_LABEL_RE.search(str(listing.get("description") or ""))
    if label:
        return label.group(1)
    return ""


def _collapse_waist_inseam(raw: str) -> tuple[str, str]:
    """Return `(waist, inseam)` when `raw` is waist x inseam. Inseam is blank when it copies the waist."""
    paired = _WAIST_INSEAM_RE.fullmatch(raw)
    if not paired:
        return "", ""
    waist, inseam = paired.group(1), paired.group(2)
    return waist, inseam if inseam != waist else ""


def _store_bottoms_inseam(listing: dict, inseam: str) -> bool:
    if not inseam:
        return False
    ebay = listing.get("ebay_specifics")
    ebay = dict(ebay) if isinstance(ebay, dict) else {}
    if str(ebay.get("inseam") or "").strip():
        return False
    ebay["inseam"] = inseam
    listing["ebay_specifics"] = ebay
    return True


def normalize_mens_bottoms_size(listing: dict) -> bool:
    """Men's bottoms size is the waist. `34x34` and `34x32` both become `34`.

    A distinct inseam is kept on the eBay Inseam field. A repeated waist (`34x34`)
    is dropped — that pair is an invented inseam, and it does not match the size dropdown.
    """
    if not isinstance(listing, dict) or not _is_mens_bottoms(listing):
        return False
    changed = False
    size = sanitize_size_value(listing.get("size"))
    waist, inseam = _collapse_waist_inseam(size)
    if waist:
        listing["size"] = waist
        changed = True
    elif size:
        waist_only = _WAIST_ONLY_RE.match(size)
        if waist_only:
            waist = waist_only.group(1)

    for key in ("size_us", "sizeUs"):
        if key not in listing:
            continue
        raw = sanitize_size_value(listing.get(key))
        key_waist, key_inseam = _collapse_waist_inseam(raw)
        if not key_waist:
            continue
        waist = waist or key_waist
        inseam = inseam or key_inseam
        if raw != waist:
            listing[key] = waist
            changed = True

    from vendoo_studio.services.seller_overrides import field_locked

    for specifics_key in _SIZE_SPECIFIC_KEYS:
        block = listing.get(specifics_key)
        if not isinstance(block, dict) or "size" not in block:
            continue
        if field_locked(listing, specifics_key[: -len("_specifics")], "size"):
            continue
        raw = sanitize_size_value(block.get("size"))
        key_waist, key_inseam = _collapse_waist_inseam(raw)
        if not key_waist:
            continue
        waist = waist or key_waist
        inseam = inseam or key_inseam
        target = waist
        if str(block.get("size") or "").strip() != target:
            listing[specifics_key] = {**block, "size": target}
            changed = True

    if waist and not str(listing.get("size") or "").strip():
        listing["size"] = waist
        changed = True

    if not inseam and waist:
        found = _bottoms_inseam(listing, waist)
        if found and found != waist:
            inseam = found
    if _store_bottoms_inseam(listing, inseam):
        changed = True
    return changed


def align_size_fields(listing: dict, *, previous_size: str | None = None) -> bool:
    """Collapse men's bottoms to a waist size and make the title match that size."""
    if not isinstance(listing, dict):
        return False
    changed = False
    if normalize_mens_bottoms_size(listing):
        changed = True
    if propagate_general_size(listing, previous_size=previous_size):
        changed = True
    if sync_title_size(listing):
        changed = True
    from vendoo_studio.services.bottoms_sizes import apply_bottoms_sizes

    if apply_bottoms_sizes(listing):
        changed = True
    return changed


_TITLE_SIZE_TOKEN_RE = re.compile(
    # Two digits at most, so a model number like 501 is never mistaken for a size.
    r"(?i)^(?:x{0,3}s|m|x{0,3}l|os|p[sml]|\d{1,2}[x\u00d7/]\d{1,2}|\d{1,2}(?:\.5)?[wl]?)$"
)


def sync_title_size(listing: dict) -> bool:
    """Make the title carry the size field verbatim — Brand Size Vibe Item Color Fit."""
    if not isinstance(listing, dict):
        return False
    title = str(listing.get("title") or "").strip()
    size = sanitize_size_value(listing.get("size"))
    if not title or not size:
        return False
    tokens = title.split()
    brand_tokens = [part for part in str(listing.get("brand") or "").strip().split() if part]
    slot = len(brand_tokens)
    if [token.lower() for token in tokens[:slot]] != [part.lower() for part in brand_tokens]:
        # A size-first title omitted the known brand. Restore the existing
        # value rather than ask the model to invent or rediscover one.
        size_tokens = size.split()
        if brand_tokens and [token.lower() for token in tokens[:len(size_tokens)]] == [part.lower() for part in size_tokens]:
            tokens[:0] = brand_tokens
        else:
            slot = 0
    size_tokens = size.split()
    normalized_size = [part.lower() for part in size_tokens]
    end = slot
    # Match complete sizes (including IT 42 / One Size), and collapse repetitions
    # left by earlier saves. Re-running this on a correct title changes nothing.
    while [token.strip(",").lower() for token in tokens[end:end + len(size_tokens)]] == normalized_size:
        end += len(size_tokens)
    if end > slot:
        tokens[slot:end] = size_tokens
    elif slot < len(tokens) and _TITLE_SIZE_TOKEN_RE.match(tokens[slot].strip(",")):
        existing = tokens[slot].strip(",")
        # Petite "PM" is the tag. Replacing it with the letter "M" drops the P.
        size_type = str(listing.get("sizeType") or "").casefold()
        if size_type in {"petite", "petites"} and existing.casefold() == f"p{size.casefold()}":
            pass
        else:
            tokens[slot:slot + 1] = size_tokens
    else:
        tokens[slot:slot] = size_tokens
    updated = " ".join(tokens)
    # Titles cap at 80 characters; leave a full one alone rather than truncate it.
    if updated == title or len(updated) > 80:
        return False
    listing["title"] = updated
    return True


def ebay_size(listing: dict, block: dict, size: str) -> str:
    """The general size as eBay's form spells it: one of the category's own Size choices when they are known."""
    from vendoo_studio.models.ebay_fields import ebay_optional_raw, ebay_size_for_type
    from vendoo_studio.models.sizes import size_choice

    spelled = ebay_size_for_type(size, ebay_optional_raw(block, "sizeType") or listing.get("sizeType"))
    choices = ebay_size_choices(listing, block)
    return size_choice(spelled, choices) or size_choice(size, choices) or spelled


def ebay_size_choices(listing: dict, block: dict | None = None) -> list[str]:
    """The Size choices of the listing's eBay category, from the cached category schema, or none."""
    from vendoo_studio.services.category_fields import load_fields

    ids = listing.get("marketplace_category_ids")
    block = block if isinstance(block, dict) else {}
    category_id = str((ids.get("ebay") if isinstance(ids, dict) else "") or block.get("categoryId") or "").strip()
    if not category_id:
        return []
    try:
        specs = load_fields("ebay", category_id) or {}
    except Exception:  # noqa: BLE001 - without a cached schema the size keeps eBay's usual spelling
        log.info("No cached eBay schema for %s", category_id)
        return []
    spec = next((spec for spec in specs.values() if (spec.display or spec.key).strip().casefold() == "size"), None)
    if spec is None:
        return []
    labels = list(spec.options.values())
    for scale in spec.scales.values():
        labels.extend((scale.get("options") or {}).values())
    return labels


def propagate_general_size(listing: dict, *, previous_size: str | None = None) -> bool:
    """Mirror general size into marketplace size slots that were following it.

    Depop keeps a size the seller set on that form. It follows the tag when it
    is empty, "Other", or still a copy of the general size, and a kids'
    category takes the tag as Depop's age (XL (18/20) is 16 years).
    """
    if not isinstance(listing, dict):
        return False
    size = sanitize_size_value(listing.get("size"))
    if not size:
        return False
    if str(listing.get("size") or "").strip() != size:
        listing["size"] = size
    changed = False
    from vendoo_studio.models.depop_fields import depop_size_for
    from vendoo_studio.services.seller_overrides import field_locked

    previous = sanitize_size_value(previous_size) if previous_size is not None else None
    for specifics_key in _SIZE_SPECIFIC_KEYS:
        block = listing.get(specifics_key)
        if not isinstance(block, dict):
            continue
        # Skip here, not only in preserving_locks: send readiness calls this directly.
        if field_locked(listing, specifics_key[: -len("_specifics")], "size"):
            continue
        current = str(block.get("size") or "").strip()
        target = size
        if specifics_key == "ebay_specifics":
            target = ebay_size(listing, block, size)
        elif specifics_key == "depop_specifics":
            # A kids' age is written even where Depop had no size yet: the
            # letter on the tag is not one of its choices.
            target = depop_size_for(size, listing)
            if "size" not in block and target == size:
                continue
            if current and current.casefold() != "other" and current not in {target, size, previous}:
                continue
        elif "size" not in block:
            continue
        if current == target:
            continue
        listing[specifics_key] = {**block, "size": target}
        changed = True
    return changed


def apply_send_readiness_fixes(listing: dict) -> bool:
    """Deterministic fixes so generated listings clear common Send blockers."""
    from vendoo_studio.models.validation import normalize_listing_dropdowns
    from vendoo_studio.services.user_settings import get_ebay_inventory_location, package_dimensions_string

    if not isinstance(listing, dict):
        return False
    changed = clear_not_applicable_values(listing)
    if normalize_listing_dropdowns(listing):
        changed = True
    if sanitize_listing_sizes(listing):
        changed = True
    if align_size_fields(listing):
        changed = True
    if strip_pricing_from_description(listing):
        changed = True
    if strip_uncertainty_from_description(listing):
        changed = True
    if strip_empty_flaws_from_description(listing):
        changed = True
    from vendoo_studio.services.listing_carryover import list_flaws_in_description
    from vendoo_studio.services.skill_formulas import description_lists_flaws

    if description_lists_flaws():
        listed = list_flaws_in_description(str(listing.get("description") or ""))
        if listed != str(listing.get("description") or ""):
            listing["description"] = listed
            changed = True
    if ensure_physical_description(listing):
        changed = True
    if not str(listing.get("package_dimensions_in") or "").strip():
        listing["package_dimensions_in"] = package_dimensions_string()
        changed = True
    if not str(listing.get("zipCode") or listing.get("zip_code") or "").strip():
        location = get_ebay_inventory_location()
        if location:
            listing["zipCode"] = location
            changed = True
    return changed


def _validation_blockers(listing: dict) -> list[dict]:
    from vendoo_studio.models.validation import validate_listing
    from vendoo_studio.services.marketplaces import get_selected_marketplaces

    result = validate_listing(
        listing,
        require_photos=False,
        selected_marketplaces=get_selected_marketplaces(),
    )
    return list(result.errors or [])


async def fill_validation_gaps(provider, listing: dict, *, analysis: str = "", notes: str = "") -> dict | None:
    """One model pass to clear remaining Send validation errors."""
    if provider is None or not isinstance(listing, dict):
        return None
    import copy

    from vendoo_studio.services.listing_patch import apply_json_patch, extract_json_patch
    from vendoo_studio.services.seller_overrides import get_path, locked_paths
    from vendoo_studio.services.skill_formulas import listing_formula_rules

    blockers = _validation_blockers(copy.deepcopy(listing))
    locked = locked_paths(listing)
    protected = {"category_path", "category_id", "marketplace_categories", "marketplace_category_ids",
                 "marketplace_category_objects", "direct_category_ids", "sku", "seller_fields"}
    allowed = {}
    for error in blockers:
        field = str(error.get("field") or "")
        if not field or field.split(".")[0].startswith("_") or field.split(".")[0] in protected:
            continue
        if any(field == path or field.startswith(path + ".") or path.startswith(field + ".") for path in locked):
            continue
        # A container error does not authorize replacement of all its fields.
        value = listing
        for key in field.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, dict):
            continue
        pointer = "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in field.split("."))
        allowed[pointer] = field
    if not allowed:
        return None
    from vendoo_studio.services.fill_log import prompt_options

    requirements = []
    for contract in (listing.get("_native_forms") or {}).values():
        for row in contract.get("fields") or []:
            if row.get("path") not in allowed.values():
                continue
            options, complete = prompt_options(row.get("options") or [], analysis)
            requirements.append({
                "path": row["path"], "options": options, "options_complete": complete,
                **{key: row[key] for key in ("input_type", "multi", "max_values", "min_value", "max_value", "max_length")
                   if key in row},
            })
    payload = {
        "validation_errors": blockers,
        "native_requirements": requirements,
        "allowed_paths": list(allowed),
        "photo_analysis": (analysis or "")[:8000],
        "seller_notes": (notes or "")[:4000],
        # Exclude bulky native schemas and private inventory metadata. Bound
        # evidence before JSON encoding; never cut a serialized JSON document.
        "current_values": {path: get_path(listing, field) for path, field in allowed.items()},
        "item": {field: listing.get(field) for field in (
            "title", "description", "brand", "size", "condition", "department", "category_path",
            "price", "weight_lb", "weight_oz", "package_dimensions_in")},
    }
    from vendoo_studio.providers.structured import repair_schema

    native = callable(getattr(provider, "structured_chat", None))
    instruction = ("Return only a JSON object with a patches array of add or replace operations."
                   if native else "Return only a JSON Patch array of add or replace operations.")
    messages = [
        {"role": "system", "content": FINALIZE_GAPS_PROMPT.format(formulas=listing_formula_rules(), output_instruction=instruction)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        if native:
            text = await collect_provider_text(provider, messages, output_schema=repair_schema(list(allowed)))
        else:
            text = await collect_provider_text(provider, messages)
    except Exception:
        log.exception("listing validation-gap fill failed")
        return None
    patches = extract_json_patch(text)
    if not patches:
        return None
    # Reject the complete reply if it attempts an unauthorized change.
    if any(op.get("path") not in allowed or op.get("op") not in {"add", "replace"}
           or "value" not in op for op in patches):
        log.warning("discarding validation repair outside allowed fields")
        return None
    updated = apply_json_patch(listing, patches)
    if "/description" in allowed:
        from vendoo_studio.services.vendoo_import import parse_notes

        apply_seller_description_facts(updated, parse_notes(notes))
    before = {(error["field"], error["message"]) for error in blockers}
    after = {(error["field"], error["message"]) for error in _validation_blockers(copy.deepcopy(updated))}
    if after - before or not before - after:
        return None
    return updated


# Keys and labels mirror GARMENTS in src/components/garmentMeasurements.ts. Pants cover
# shorts too, since both take the same measurements.
GARMENT_MEASUREMENTS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "top": ("Top", (("pitToPit", "Pit to pit"), ("length", "Length"), ("sleeve", "Sleeve"))),
    "pants": (
        "Pants",
        (("waist", "Waist"), ("rise", "Rise"), ("inseam", "Inseam"), ("legOpening", "Leg opening")),
    ),
    "skirt": ("Skirt", (("waist", "Waist"), ("hip", "Hip"), ("length", "Length"))),
}


def _garment_measurements(parsed: dict) -> tuple[str, str]:
    """Only the selected garment's label and measurements; the others stay saved but unused."""
    garment = parsed.get("garment")
    if garment == "shorts":  # saved before shorts folded into pants
        garment = "pants"
    if garment not in GARMENT_MEASUREMENTS:
        return "", ""
    label, fields = GARMENT_MEASUREMENTS[garment]
    measurements = parsed.get("measurements")
    values = None
    if isinstance(measurements, dict):
        values = measurements.get(garment)
        if values is None and garment == "pants":
            values = measurements.get("shorts")
    if not isinstance(values, dict):
        return "", ""
    parts = [
        f'{name}: {str(values.get(key) or "").strip()}"'
        for key, name in fields
        if str(values.get(key) or "").strip()
    ]
    return (label, "; ".join(parts)) if parts else ("", "")


def _description_measurements(parsed: dict) -> str:
    """The measurements the description must show: typed ones, else carried by Regenerate."""
    _label, typed = _garment_measurements(parsed)
    return typed or str(parsed.get("descriptionMeasurements") or "").strip()


def _comparable(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def apply_seller_description_facts(listing: dict, seller: dict) -> bool:
    """Write the seller's measurements and flaws into the description verbatim.

    The model is told both, but nothing else stops it writing "See photos" or
    "none noted" over them — which is how Regenerate used to lose them.
    """
    from vendoo_studio.services.listing_carryover import (
        description_block,
        flaw_items,
        render_flaws_block,
        set_description_block,
        without_see_photos,
    )
    from vendoo_studio.services.skill_formulas import description_lists_flaws

    if not isinstance(listing, dict):
        return False
    desc = str(listing.get("description") or "").strip()
    updated = desc
    flaws = without_see_photos(seller.get("knownFlaws"))
    # A model that names the seller's flaws and adds ones it saw keeps its block.
    seller_flaws = "; ".join(flaw_items(flaws))
    described = _comparable(description_block(updated, "flaws?"))
    if seller_flaws and _comparable(seller_flaws) not in described:
        block = render_flaws_block(flaws, as_list=description_lists_flaws())
        if block:
            updated = set_description_block(updated, "flaws?", block, before="measurements?")
    measurements = _description_measurements(seller)
    if measurements and description_block(updated, "measurements?") != measurements.rstrip("."):
        updated = set_description_block(updated, "measurements?", f"Measurements: {measurements}")
    if updated == desc:
        return False
    listing["description"] = updated
    return True


def _asking_price(notes: dict) -> float:
    """The price the seller confirmed, carried into Item Details by Regenerate."""
    try:
        amount = float(str(notes.get("askingPrice") or "").strip() or 0)
    except ValueError:
        return 0.0
    return amount if amount > 0 else 0.0


def seller_item_details(notes: str | None) -> str:
    if not notes:
        return ""
    try:
        parsed = json.loads(notes)
    except json.JSONDecodeError:
        return f"Seller notes: {notes}"
    if not isinstance(parsed, dict):
        return f"Seller notes: {notes}"

    lines = []
    seller_notes = str(parsed.get("sellerNotes") or "").strip()
    if seller_notes:
        lines.append(f"- Notes: {seller_notes}")
    if parsed.get("condition"):
        lines.append(f"- Condition: {parsed['condition']}")
    category = str(parsed.get("categoryOverride") or "").strip()
    if category:
        lines.append(f"- Category: {category}")
    sku = str(parsed.get("sku") or "").strip()
    if sku:
        lines.append(f"- SKU: {sku} (keep this exact SKU)")
    labels = str(parsed.get("vendooLabels") or "").strip()
    if labels:
        lines.append(f"- Labels: {labels}")
    if parsed.get("cog"):
        lines.append(f"- Cost of goods: ${parsed['cog']}")
    asking = _asking_price(parsed)
    if asking:
        lines.append(f"- List price: ${asking:g} (the seller set this — use it exactly)")
    if parsed.get("packageDimensions"):
        lines.append(f"- Package dimensions: {parsed['packageDimensions']}")
    label, measurements = _garment_measurements(parsed)
    if measurements:
        lines.append(f"- Measurements ({label.lower()}): {measurements}")
    else:
        # Carried off an earlier description by Regenerate: still seller facts.
        carried = str(parsed.get("descriptionMeasurements") or "").strip()
        if carried:
            lines.append(f"- Measurements: {carried}")
    flaws = str(parsed.get("knownFlaws") or "").strip()
    if flaws:
        lines.append(f"- Known flaws: {flaws}")
    if not lines:
        return ""
    return "Known item details from the seller:\n" + "\n".join(lines)


def listing_save_summary(
    db, conv_id: str, listing: dict, *, waiting: bool = False, repaired: bool = False
) -> str:
    """Honest post-save status based on validation and discovered field gaps."""
    from vendoo_studio.models.validation import validate_listing
    from vendoo_studio.services.listing_field_gaps import remaining_discovered_gap_count
    from vendoo_studio.services.marketplaces import get_selected_marketplaces

    photos = len(ConversationRepo(db).get_photos(conv_id))
    validation = validate_listing(
        listing,
        photos,
        require_photos=False,
        selected_marketplaces=get_selected_marketplaces(),
    )
    missing = len(validation.errors)
    discovered = remaining_discovered_gap_count(db, listing)
    lead = "Listing repaired and saved" if repaired else "Listing saved"
    if waiting:
        if missing or discovered:
            parts = []
            if missing:
                parts.append(f"{missing} required field(s) still missing in Anteroom")
            if discovered:
                parts.append("some discovered fields are still empty")
            return (
                f"{lead} with questions outstanding. "
                + " — ".join(parts)
                + ". Finish them in Forms before Send."
            )
        return f"{lead} with questions outstanding. Answer them in chat before Send."
    if missing or discovered:
        parts = []
        if missing:
            parts.append(f"{missing} required field(s) still missing in Anteroom")
        if discovered:
            # No count: Forms and chat would otherwise quote two different
            # numbers for "empty fields" and read as contradicting each other.
            parts.append("some discovered fields are still empty")
        return (
            f"{lead}. "
            + " — ".join(parts)
            + ". Review Forms for what still needs chat, then Send when ready."
        )
    return (
        f"{lead}. Required and discovered Anteroom fields are filled. "
        "Review Forms, then Send when the draft looks right."
    )


def _ready_note(*, repaired: bool, finalized: bool, blockers: list) -> str:
    blocker_text = "; ".join(
        str(err.get("message") or err.get("field") or "issue") for err in (blockers or [])[:6]
    )
    if blockers:
        lead = "Listing repaired after a readiness pass" if repaired else "Listing extracted after a readiness pass"
        return f"{lead}. Remaining send blockers: {blocker_text}. Still ready for review."
    if repaired and finalized:
        return "Listing repaired and required fields filled. Ready for review."
    if finalized:
        return "Listing extracted and required fields filled. Ready for review."
    if repaired:
        return "Listing repaired from malformed model output and ready for review."
    return "Listing extracted and ready for review."


# Mercari caps the description at 1,000. Poshmark caps it at 1,500. Depop caps
# the title, a blank line, and the description together at 1,000, and a title
# is at most 80 characters. 918 is the longest description that still fits.
MERCARI_DESCRIPTION_MAX = 1000
POSHMARK_DESCRIPTION_MAX = 1500
DEPOP_POST_MAX = 1000
TITLE_MAX_FOR_DESCRIPTION = 80
_DEPOP_TITLE_GAP = 2
_MEASUREMENTS_HEAD = re.compile(r"(?is)^measurements\s*:")


def description_character_limit(title: str) -> int:
    """How long this description can be and still list on every marketplace."""
    title_len = len(str(title or "").strip())
    gap = _DEPOP_TITLE_GAP if title_len else 0
    depop_room = DEPOP_POST_MAX - title_len - gap
    return max(0, min(MERCARI_DESCRIPTION_MAX, POSHMARK_DESCRIPTION_MAX, depop_room))


def _cut_at_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.strip()
    if limit <= 0:
        return ""
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,;:-").strip()


def fit_description(text: str, limit: int) -> str:
    """Shorten a description to ``limit`` without dropping its Measurements block."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    measurements = ""
    body = paragraphs
    if paragraphs and _MEASUREMENTS_HEAD.match(paragraphs[-1]):
        measurements = paragraphs[-1]
        body = paragraphs[:-1]
    if measurements and len(measurements) + (2 if body else 0) > limit:
        return _cut_at_word(text, limit)
    room = limit - (len(measurements) + 2 if measurements else 0)
    kept: list[str] = []
    for para in body:
        gap = 2 if kept else 0
        if len(para) + gap <= room:
            kept.append(para)
            room -= len(para) + gap
            continue
        piece = _cut_at_word(para, room - gap)
        if piece:
            kept.append(piece)
        break
    fitted = "\n\n".join([*kept, *([measurements] if measurements else [])]).strip()
    if len(fitted) > limit:
        fitted = _cut_at_word(fitted, limit)
    return fitted


def cap_listing_description(listing: dict) -> bool:
    """Cut a generated description to the shortest marketplace limit."""
    if not isinstance(listing, dict):
        return False
    description = str(listing.get("description") or "")
    limit = description_character_limit(str(listing.get("title") or ""))
    if len(description.strip()) <= limit:
        return False
    listing["description"] = fit_description(description, limit)
    return listing["description"] != description


def persist_generated_listing(
    db,
    conv_id: str,
    full_text: str,
    *,
    source: str = "model",
    parsed: dict | None = None,
    repaired: bool = False,
    provider_name: str = "xiaomi-mimo",
    model_name: str = "mimo-v2.5-pro",
    announce: bool = True,
    expected_revision_id: str | None = None,
) -> dict | None:
    repo = ConversationRepo(db)
    if full_text:
        repo.add_message(conv_id, "assistant", full_text, provider=provider_name, model=model_name)

    listing = parsed if isinstance(parsed, dict) else extract_listing_json(full_text)
    if not listing:
        log.warning("listing generation produced no JSON for %s", conv_id)
        return None

    import copy

    revisions = ListingRepo(db).get_revisions(conv_id)
    seed = revisions[0].listing_json if revisions else {}
    native_blocks = {market: copy.deepcopy(listing.get(f"{market}_specifics") or {})
                     for market in seed.get("_native_forms") or {}}
    RegistryService(db).merge_learned_fields(listing)
    # Dimensions are a seller preference, not a model inference. Start every
    # generated listing from the saved default; an explicit per-listing value
    # in Item Details may override it below.
    from vendoo_studio.services.user_settings import get_ebay_inventory_location, package_dimensions_string

    listing["package_dimensions_in"] = package_dimensions_string()
    location = get_ebay_inventory_location()
    if location:
        listing["zipCode"] = location
    elif str(listing.get("zipCode") or "") == "70125":
        listing.pop("zipCode", None)
    apply_send_readiness_fixes(listing)
    from vendoo_studio.services.vinted_listing import apply_vinted_listing

    apply_vinted_listing(listing)
    if revisions:
        selected = revisions[0].listing_json
        if selected.get("marketplace_categories"):
            listing["category_path"] = selected["category_path"]
            listing["marketplace_categories"] = dict(selected["marketplace_categories"])
    # Regenerate carries the seller's SKU, package size, confirmed price,
    # measurements and flaws into notes; keep them on the new listing even if
    # the model invents others. The SKU is the seller's alone: none is made up.
    conv = repo.get(conv_id)
    if conv:
        from vendoo_studio.services.vendoo_import import parse_notes

        seller = parse_notes(conv.notes)
        apply_seller_description_facts(listing, seller)
        seller_sku = str(seller.get("sku") or "").strip()
        if seller_sku:
            listing["sku"] = seller_sku
        else:
            listing.pop("sku", None)
        package = str(seller.get("packageDimensions") or "").strip()
        if package:
            listing["package_dimensions_in"] = package
        from vendoo_studio.services.seller_overrides import is_locked, materialize_prices

        previous_listing = revisions[0].listing_json if revisions and isinstance(revisions[0].listing_json, dict) else {}
        if previous_listing.get("seller_fields"):
            listing["seller_fields"] = list(previous_listing["seller_fields"])
        from vendoo_studio.services.marketplace_media import keep_media

        keep_media(previous_listing, listing)
        asking = _asking_price(seller)
        if asking and not is_locked(listing, "price"):
            listing["price"] = asking
        elif is_locked(listing, "price") and previous_listing.get("price") is not None:
            listing["price"] = previous_listing.get("price")
        materialize_prices(listing)
        specifics = listing.get("poshmark_specifics")
        if not isinstance(specifics, dict):
            specifics = {}
        specifics["originalPrice"] = 0
        listing["poshmark_specifics"] = specifics
    from vendoo_studio.services.native_contract import constrain_generated

    for market, block in native_blocks.items():
        listing[f"{market}_specifics"] = block
    constrain_generated(listing, seed)
    cap_listing_description(listing)
    if revisions:
        from vendoo_studio.services.seller_overrides import capture_locked, restore_locked, materialize_prices

        restore_locked(listing, capture_locked(revisions[0].listing_json))
        materialize_prices(listing)
    ListingRepo(db).save_revision(conv_id, listing, source=source, expected_revision_id=expected_revision_id)

    if announce:
        repo.add_message(
            conv_id,
            "system",
            _ready_note(repaired=repaired, finalized=False, blockers=_validation_blockers(listing)),
            provider="system",
            model="",
        )
    return listing


async def persist_generated_listing_with_repair(
    db,
    conv_id: str,
    full_text: str,
    provider,
    *,
    source: str = "model",
    final_announce: bool = True,
    expected_revision_id: str | None = None,
    expected_photo_fingerprint: str | None = None,
) -> dict | None:
    """Persist listing JSON, repairing parse errors and clearing Send validation gaps."""
    provider_name = getattr(provider, "name", "xiaomi-mimo")
    model_name = getattr(provider, "listing_model", "mimo-v2.5-pro")
    if expected_revision_id is None:
        expected_revision_id = ConversationRepo(db).current_revision_id(conv_id) or ""
    parsed = extract_listing_json(full_text)
    repaired = False
    if not parsed:
        repaired_json = await repair_listing_json(provider, full_text)
        if repaired_json:
            log.info("repaired malformed listing JSON for %s", conv_id)
            parsed = repaired_json
            repaired = True

    def check_photos() -> None:
        if expected_photo_fingerprint is not None:
            db.expire_all()
            if photo_fingerprint(ConversationRepo(db).get_photos(conv_id)) != expected_photo_fingerprint:
                raise RuntimeError("Photos changed during generation. Generate again to read the current photos.")

    check_photos()
    listing = persist_generated_listing(
        db,
        conv_id,
        full_text,
        source=source,
        parsed=parsed,
        repaired=repaired,
        provider_name=provider_name,
        model_name=model_name,
        announce=False,
        expected_revision_id=expected_revision_id,
    )
    if not listing:
        return None

    expected_revision_id = ConversationRepo(db).current_revision_id(conv_id)
    conv = ConversationRepo(db).get(conv_id)
    analysis = current_photo_analysis(ConversationRepo(db), conv_id) or ""
    notes = str(getattr(conv, "notes", "") or "")
    finalized = False
    if _validation_blockers(listing):
        updated = await fill_validation_gaps(provider, listing, analysis=analysis, notes=notes)
        if isinstance(updated, dict) and updated:
            check_photos()
            listing = updated
            ListingRepo(db).save_revision(conv_id, listing, source="generation_finalize", expected_revision_id=expected_revision_id)
            finalized = True

    if final_announce:
        ConversationRepo(db).add_message(
            conv_id,
            "system",
            _ready_note(repaired=repaired, finalized=finalized, blockers=_validation_blockers(listing)),
            provider="system",
            model="",
        )
    return listing
