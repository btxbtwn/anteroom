"""Prompt construction for listing chat and listing generation."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from vendoo_studio.config import PHOTOS_DIR, skills_dir
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.chat_citations import (
    citations_to_plain_text,
    expand_citations_for_provider,
)
from vendoo_studio.services.comp_research import research_sold_comps
from vendoo_studio.services.generation_history import seller_history_prompt
from vendoo_studio.services.listing_evidence import generation_evidence_prompt
from vendoo_studio.services.listing_generate import (
    analysis_with_photo_count,
    analyze_photos_with_tag_retry,
    latest_photo_analysis,
    photo_analysis_failure,
    photo_analysis_usable,
    require_photo_analysis,
    seller_item_details,
)
from vendoo_studio.services.listing_provider import get_photo_provider
log = logging.getLogger("vendoo_studio.chat")


def load_skill_rules(query: str = "", db: Session | None = None) -> str:
    from vendoo_studio.services.skill_formulas import with_pinned_formulas

    skill_md = skills_dir() / "list-this" / "SKILL.md"
    template_md = skills_dir() / "list-this" / "references" / "vendoo_listing_template.md"

    def _file_rules() -> str:
        parts = []
        if skill_md.exists():
            parts.append(skill_md.read_text())
        if template_md.exists():
            parts.append(template_md.read_text())
        return with_pinned_formulas("\n\n---\n\n".join(parts) if parts else "")

    if db is not None and str(query or "").strip():
        try:
            from vendoo_studio.services.catalog_index import relevant_skill_rules
            rules = relevant_skill_rules(db, query)
            if str(rules or "").strip():
                return with_pinned_formulas(rules)
        except Exception:
            log.exception("catalog skill rules unavailable; falling back to SKILL.md")
    return _file_rules()


def learned_fields_prompt(db: Session, conv_id: str) -> str:
    from vendoo_studio.services.registry import RegistryService

    category_path = None
    revisions = ListingRepo(db).get_revisions(conv_id)
    if revisions and isinstance(revisions[0].listing_json, dict):
        category_path = revisions[0].listing_json.get("category_path") or None
    from vendoo_studio.services.category_catalog import schema_context
    text = RegistryService(db).generation_context(category_path) + schema_context(db, category_path or "")
    if not text:
        return ""
    return f"\n\n--- Learned fields ---\n\n{text}"


def current_listing_prompt(db: Session, conv_id: str) -> str:
    import json
    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions or not isinstance(revisions[0].listing_json, dict):
        return ""
    listing = revisions[0].listing_json
    ebay = listing.get("ebay_specifics") if isinstance(listing.get("ebay_specifics"), dict) else {}
    return (
        "\n\n--- Current listing ---\n"
        f"Saved listing JSON: {json.dumps(listing, ensure_ascii=False)}\n"
        f"department: {listing.get('department') or ''}\n"
        f"category_path: {listing.get('category_path') or ''}\n"
        f"ebay_specifics.department: {(ebay or {}).get('department') or ''}\n"
        "If the seller changes gender or category, replace department and category_path together. "
        "category_path names the last category in the branch, such as Men > Shirts > Tees. "
        "Do not leave a women's category on a men's item.\n"
    )


async def build_chat_messages(conv_id: str, db: Session, user_message: str) -> list[dict]:
    repo = ConversationRepo(db)
    history = repo.get_messages(conv_id)
    photos = repo.get_photos(conv_id)
    conv = repo.get(conv_id)
    notes = (conv.notes if conv else "") or ""

    # Quotes the seller cited are links in the stored message; the rule search
    # and the request heuristics read them as the text the seller sees.
    user_message_text = citations_to_plain_text(user_message)

    skill_rules = load_skill_rules(user_message_text, db)

    photo_analysis_text = ""
    comps_text = ""
    evidence: dict = {}
    existing_analysis = latest_photo_analysis(history)
    if photos and existing_analysis and photo_analysis_usable(existing_analysis):
        photo_analysis_text = analysis_with_photo_count(len(photos), existing_analysis)
        skill_rules = load_skill_rules(
            f"{user_message_text}\n{photo_analysis_text}\n{seller_item_details(notes)}",
            db,
        )
    elif photos:
        provider = get_photo_provider()
        if provider:
            paths = [str(Path(PHOTOS_DIR) / p.stored_filename) for p in photos]
            # End the read transaction so the session does not hold a pool
            # connection while the model reads photos and researches comps.
            db.commit()
            try:
                result = await analyze_photos_with_tag_retry(provider, paths, notes="", listing_rules=skill_rules[:8000])
            except Exception as exc:
                raise photo_analysis_failure(exc) from exc
            evidence, analysis_note = require_photo_analysis(result)
            analysis_note = analysis_note.replace(
                "Photo analysis:",
                "Photo analysis of the uploaded product images:",
                1,
            )
            repo.add_message(conv_id, "system", analysis_note)
            photo_analysis_text = analysis_with_photo_count(len(photos), analysis_note)
            skill_rules = load_skill_rules(
                f"{user_message_text}\n{photo_analysis_text}\n{seller_item_details(notes)}",
                db,
            )
            db.commit()
            comps_text = await research_sold_comps(photo_analysis_text, evidence)
            if comps_text:
                repo.add_message(conv_id, "system", comps_text, provider="web-search", model="web-search")
        else:
            photo_analysis_text = analysis_with_photo_count(len(photos), existing_analysis)

    comps_block = f"\n\n--- Sold comps ---\n\n{comps_text}\n" if comps_text else ""

    from vendoo_studio.services.fill_log import is_missing_fields_request

    ask_missing_fields = is_missing_fields_request(user_message_text)
    if ask_missing_fields:
        change_instructions = (
            "The seller asked you to fill specific empty/leftover fields.\n"
            "1. Write one short sentence confirming which fields you filled.\n"
            "2. Then include a fenced json code block with this exact shape so Anteroom saves them "
            "into the listing JSON automatically:\n"
            "```json\n"
            '{"missing_fields":[{"marketplace":"etsy","field":"Pattern","value":"Solid"}]}\n'
            "```\n"
            "Use the marketplace ids and field names from the seller request exactly. "
            "To set or clear one saved field, including a value outside the reference list, use this shape instead:\n"
            '{"fields":[{"path":"depop_specifics.style","value":[]}]}\n'
            "Paths look like sku, price, mercari_specifics.brand, poshmark_specifics.size, "
            "ebay_specifics.inseam, ebay_specifics.bestOffer.minimumOfferPrice, depop_specifics.measurements, "
            "direct_category_ids.ebay, direct_category_ids.poshmark, direct_category_ids.depop, "
            "direct_category_ids.mercari, or sellwild_specifics.nativeCategoryId. "
            "An empty value clears the field and it stays cleared. "
            "An empty mercari_specifics.brand, or the value \"No Brand/Not sure\", lists that item on Mercari "
            "with no brand and leaves the main brand unchanged. When Mercari has the same size in more than one "
            "group, set mercari_specifics.sizeType or mercari_specifics.sizeGroup (Regular, Petite, Plus, Tall). "
            "When that is empty, Anteroom uses the item's size type, then the regular or standard group.\n"
            "Bottoms use the size that marketplace accepts: women's 0-24, Petite/Plus/Tall, "
            "eBay waist plus inseam, Poshmark \"Waist 32\". "
            "eBay Best Offer is enabled (Yes or No), autoAcceptPrice, and minimumOfferPrice, "
            "and the minimum must be below the auto-accept price, which must be below the item price.\n"
            "Do not use a JSON Patch array. Do not rewrite unrelated listing fields.\n"
            "Anteroom saves the listing and the form updates.\n\n"
        )
    else:
        change_instructions = (
            "When the user asks you to change an existing listing:\n"
            "1. Write a short confirmation of what you changed (department, category, marketplace fields).\n"
            "2. Then include a JSON Patch array in a fenced json code block so the listing can be saved. "
            "The seller will not see that block.\n"
            'Example confirmation: "This is a men\'s T-shirt. I moved it to Men > Shirts > Tees."\n'
            "Example patch:\n"
            "```json\n"
            '[{"op": "replace", "path": "/department", "value": "Men"},'
            '{"op": "replace", "path": "/category_path", "value": "Men > Shirts > Tees"}]\n'
            "```\n"
            "Save a category only when it is the last one in that branch. "
            "Anteroom stores each marketplace's own category id from that path, and the listing form updates. "
            "You can set one marketplace yourself when you know its id: "
            "direct_category_ids.ebay together with direct_category_ids.ebay_path, "
            "direct_category_ids.poshmark, direct_category_ids.depop, direct_category_ids.mercari, "
            "or sellwild_specifics.nativeCategoryId. "
            "Use that marketplace's own id. "
            "On Poshmark, None is the last step when you want a category that still has narrower choices.\n\n"
        )

    system_prompt = {
        "role": "system",
        "content": (
            "You are a product listing assistant talking to a seller. Write in plain English.\n"
            "Never reply with JSON-only output, status objects, or a bare JSON Patch array.\n\n"
            + change_instructions
            +             "When generating a listing from scratch, infer every supportable field from the photos and initial seller notes, "
            "mention remaining uncertainties about the item briefly in your reply without asking questions, "
            "then the full listing JSON in a fenced json code block.\n\n"
            "If you are not changing the listing, reply in plain English only. "
            "If asked whether the listing was updated, say yes only when a system message in this "
            "conversation confirms the listing JSON was saved; otherwise say no.\n\n"
            "Key rules:\n"
            "- Never publish. Stop at saved drafts.\n"
            "- Never ask the seller clarifying questions. Infer from the photos and notes already provided.\n"
            "- Never ask the seller to upload or attach photos when product photos are already present.\n"
            "- Be conservative with brand and size: use photo/tag/logo evidence and seller notes only; leave unsupported facts empty — never invent, never ask.\n"
            "- A category is the last choice in its branch, written Department > Category > Last category, such as Men > Shirts > Tees. "
            "A group that still has a narrower choice is not saved. "
            "On Poshmark, finish with None when you want a category that still has narrower choices.\n"
            "- Title MUST follow Brand Size Vibe Item Color Fit exactly (max 80 chars) from the Formula Reference below.\n"
            "- The size in the title must be the size field verbatim. Men's bottoms size is the waist alone (34), never waist x inseam (34x34). "
            "Each marketplace form then gets the bottoms size it accepts (eBay waist and inseam, Poshmark Waist 32, women's 0-24, Petite/Plus/Tall).\n"
            "- eBay Best Offer is ebay_specifics.bestOffer.enabled (Yes or No), autoAcceptPrice, and minimumOfferPrice. "
            "The minimum must be below the auto-accept price, and that must be below the item price.\n"
            "- Condition is required on every physical listing: an exact Vendoo general value such as \"Pre-Owned - Good\". Never leave it blank.\n"
            "- Description MUST follow the physical-item formula exactly (line breaks; trendy keyword sentence, Flaws: only when the item has flaws, Measurements:) unless this is an Etsy digital download.\n"
            "- NEVER put pricing details in the description: no prices, dollar amounts, comps, sold-listing counts, market/resale value, MSRP, discounts, offers, or pricing uncertainty. Pricing lives in the price field only.\n"
            "- NEVER put uncertainties in the description: no notes about what you could not verify or read, no \"approximate\", \"estimated\", \"photo estimates\", \"uncertain\" or \"unclear\" hedges. The description is buyer-facing; uncertainties are internal and belong in your reply only.\n"
            "- Do not invent catchy titles or prose that break those formulas.\n"
            "- Resolve every applicable discovered field with a real value. Leave a field out when it does not apply to the item; never write \"Does Not Apply\".\n"
            "- Fill every discovered category/marketplace field in the JSON. Do not leave applicable fields empty.\n"
            "- Never tell the seller the listing is complete, ready, or done while any discovered field is still empty.\n"
            "- Estimate packaged shipping weight and mailer dimensions from the item type; do not ask the seller for those.\n"
            "- Depop style is up to 3 tags. Leave it empty when the seller cleared it. Do not invent tags to fill three.\n"
            "- A marketplace price follows the item price unless the seller set that marketplace's own price.\n"
            "- Depop measurements use the inches already stored for the garment. Do not invent measurements.\n"
            + (f"\n{photo_analysis_text}\n\n" if photo_analysis_text else "") +
            comps_block +
            (f"\n--- Listing Rules ---\n\n{skill_rules}" if skill_rules else "")
        ) + current_listing_prompt(db, conv_id) + learned_fields_prompt(db, conv_id),
    }

    from vendoo_studio.services.chat_field_context import chat_field_context

    system_prompt["content"] += chat_field_context(db, conv_id)
    system_prompt["content"] += _item_form_prompt(db, conv_id)
    messages = [system_prompt]
    for msg in history[-20:]:
        role = msg.role
        if role == "model":
            role = "assistant"
        # The model reads the seller's quotes as reference material; the stored
        # message keeps the links that render as chips in chat.
        content = expand_citations_for_provider(msg.text) if role == "user" else msg.text
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": expand_citations_for_provider(user_message)})
    return messages


LISTING_INSTRUCTIONS = (
    "You are a product listing generator. Generate an evidence-backed Vendoo listing JSON "
    "from the photo analysis and listing rules below.\n\n"
    "Preserve category_path and marketplace_categories from the verified category selections below. "
    "Each marketplace uses its own category tree; do not substitute another form's breadcrumb.\n\n"
    "Never make up a sku: leave it out unless the seller notes give one. "
    "Always include primaryColor, and secondaryColor "
    "when a second color is visible. Colors must be exact Vendoo General dropdown values from "
    "the listing rules below. Use Blue for navy; Navy is only a Depop option. "
    "Use Vendoo general condition values such as "
    '"Pre-Owned - Good". Keep tags to 5 or fewer. Depop needs source and age. '
    "Mercari shippingLabel must be USPS Ground Advantage.\n\n"
    "TITLE and DESCRIPTION are non-negotiable skill formulas — copy the structure from "
    "Formula Reference below. Title order is Brand Size Vibe Item Color Fit (max 80 chars), "
    "and the title's size is the size field verbatim — men's bottoms size is the waist alone (34), never 34x34. "
    "Always set condition to an exact Vendoo general value; never omit it. "
    "Physical descriptions must keep the mandatory blank lines and the Flaws:/Measurements: "
    "blocks after a short trendy keyword sentence. Do not write freeform marketing copy that breaks those formulas. "
    "When the item has no flaws, leave the Flaws: line out entirely — never write \"none noted\".\n\n"
    "If seller-provided measurements (Pit to pit, Length, Sleeve) are given, use them exactly as-is in the description.\n"
    "Do not modify, estimate, or replace seller-provided measurements.\n"
    "If the seller lists known flaws, repeat every one of them in the Flaws: block, "
    "in the seller's own words. Never drop a flaw or soften it to \"none noted\".\n"
    "Use the discovered category fields below. Fill every applicable field with a real value, and leave out any that does not apply — never write \"Does Not Apply\". "
    "Only leave a field empty when you must ask the seller a precise question in prose — and never claim the listing is complete while any applicable discovered field is still empty. "
    "Always infer and include packaged shipping weight_lb and weight_oz from the item type, size, materials, "
    "photos, seller notes, and research evidence. Pounds may be 0, but both fields must be present and their "
    "combined weight must be greater than 0. Do not ask the seller for routine shipping weight. "
    "Anteroom supplies package_dimensions_in from the seller's saved Settings default. "
    "Never invent brand, size, material, age, or other product facts without photo or seller evidence. "
    "Prefer verbatim tag text from the photo analysis for brand, size, and material. "
    "Anteroom applies generated values onto the bound Vendoo draft automatically when Chrome is connected. "
    "Choose the pricing baseline from current sold comps and relevant seller history using the "
    "canonical PRICING Formula below. Follow its evidence thresholds and live asking ceiling. "
    "Never mention pricing in the description.\n"
    "The description is buyer-facing copy about the item: never include prices, dollar amounts, comps, "
    "sold-listing counts, market or resale value, MSRP, discounts, offers, or any note about pricing "
    "confidence. Those belong in the price field only.\n\n"
    "Return ONLY one fenced ```json code block with the full listing object. "
    "No prose before or after the fence. Valid JSON only (no trailing commas).\n\n"
    "```json\n"
    "{\n"
    '  "title": "...",\n'
    '  "description": "...",\n'
    '  "price": ...,\n'
    '  "cost": ...,\n'
    '  "quantity": 1,\n'
    '  "brand": "...",\n'
    '  "condition": "...",\n'
    '  "primaryColor": "...",\n'
    '  "secondaryColor": "...",\n'
    '  "size": "...",\n'
    '  "sizeType": "...",\n'
    '  "tags": [...],\n'
    '  "weight_lb": ... ,\n'
    '  "weight_oz": ... ,\n'
    '  "package_dimensions_in": "...",\n'
    '  "ebay_specifics": {...},\n'
    '  "depop_specifics": {"source": "Preloved", "age": "Modern"},\n'
    '  "etsy_specifics": {...},\n'
    '  "poshmark_specifics": {"originalPrice": 0},\n'
    '  "mercari_specifics": {"shippingLabel": "USPS Ground Advantage"}\n'
    "}\n"
    "```"
)


def _empty_discovered_fields_prompt(db: Session, conv_id: str, evidence: str = "") -> str:
    """List discovered fields still empty so generation fills them in one pass, not a later gap round."""
    from copy import deepcopy

    from vendoo_studio.services.listing_field_gaps import collect_empty_discovered_fields
    from vendoo_studio.services.registry import RegistryService

    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions or not isinstance(revisions[0].listing_json, dict):
        return ""
    probe = deepcopy(revisions[0].listing_json)
    if not str(probe.get("category_path") or "").strip():
        return ""
    RegistryService(db).merge_learned_fields(probe)
    gaps = collect_empty_discovered_fields(db, probe)
    if not gaps:
        return ""
    import json
    from vendoo_studio.services.fill_log import prompt_options

    lines = []
    for gap in gaps:
        line = f"- {gap['marketplace']}: {gap['field']}"
        if gap.get("required"):
            line += " (required)"
        if gap.get("options"):
            options, complete = prompt_options(gap["options"], evidence or str(probe.get("title") or ""))
            line += " — allowed options: " + json.dumps(options, ensure_ascii=False)
            if not complete:
                line += " (relevant subset; other allowed options may exist)"
        lines.append(line)
    return (
        "\n\n--- Discovered fields still empty ---\n"
        "Fill each of these in the listing JSON (root fields or the marketplace *_specifics) using exact "
        "allowed options from the category fields above. Leave out a field that does not apply to the item; never write \"Does Not Apply\":\n"
        + "\n".join(lines)
    )


def _item_form_prompt(db: Session, conv_id: str) -> str:
    """The fields each marketplace form shows for this item. Same list as the editor."""
    from vendoo_studio.services.form_schema import form_schema_prompt

    revisions = ListingRepo(db).get_revisions(conv_id)
    listing = revisions[0].listing_json if revisions and isinstance(revisions[0].listing_json, dict) else None
    return form_schema_prompt(listing)


def _live_category_fields_prompt(db: Session, conv_id: str) -> str:
    """eBay's and Etsy's own field names, when category choice already cached them."""
    from vendoo_studio.services.direct_categories import live_fields_prompt

    revisions = ListingRepo(db).get_revisions(conv_id)
    listing = revisions[0].listing_json if revisions and isinstance(revisions[0].listing_json, dict) else {}
    return live_fields_prompt(listing)


def listing_generation_messages(
    skill_rules: str,
    item_details: str,
    analysis_text: str,
    db: Session,
    conv_id: str,
    comps_text: str = "",
    photo_count: int = 0,
) -> list[dict]:
    comps_block = f"\n\n--- Sold comps ---\n\n{comps_text}" if comps_text else ""
    photo_line = (
        f"The seller already uploaded {photo_count} product photo(s). "
        "Never ask them to attach or re-upload photos — generate the listing now.\n\n"
        if photo_count
        else ""
    )
    # Static instructions and rules lead so provider prefix caches hit across items;
    # per-item evidence follows.
    field_evidence = f"{analysis_text}\n{item_details}"
    system_content = (
        f"{LISTING_INSTRUCTIONS}\n\n"
        f"--- Listing Rules ---\n\n{skill_rules}"
        f"{learned_fields_prompt(db, conv_id)}"
        "\n\n--- This item ---\n\n"
        f"{photo_line}"
        f"{item_details}\n\n"
        f"{analysis_text}"
        f"{comps_block}"
        f"{seller_history_prompt(db, conv_id, analysis_text)}"
        f"{generation_evidence_prompt(db, conv_id)}"
        f"{current_listing_prompt(db, conv_id)}"
        f"{_empty_discovered_fields_prompt(db, conv_id, field_evidence)}"
        f"{_live_category_fields_prompt(db, conv_id)}"
        f"{_item_form_prompt(db, conv_id)}"
    )
    return [
        {"role": "system", "content": system_content},
        {
            "role": "user",
            "content": (
                f"Generate a complete listing from the {photo_count} uploaded product photos. "
                "Reply with only the ```json listing block."
                if photo_count
                else "Generate a complete listing from these product photos. Reply with only the ```json listing block."
            ),
        },
    ]
