"""Prompt construction for listing chat and listing generation."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.chat_citations import (
    citations_to_plain_text,
    expand_citations_for_provider,
)
from vendoo_studio.services.comp_research import research_sold_comps
from vendoo_studio.services.photo_store import photo_store
from vendoo_studio.services.generation_history import seller_history_prompt
from vendoo_studio.services.item_context import item_decisions_prompt
from vendoo_studio.services.listing_generate import (
    analysis_with_photo_count,
    analyze_photos_with_tag_retry,
    latest_photo_analysis,
    photo_analysis_failure,
    photo_fingerprint,
    photo_analysis_usable,
    require_photo_analysis,
    seller_item_details,
)
from vendoo_studio.services.listing_provider import get_photo_provider
log = logging.getLogger("vendoo_studio.chat")


def load_skill_rules(query: str = "", db: Session | None = None) -> str:
    from vendoo_studio.services.catalog_index import relevant_skill_rules

    return relevant_skill_rules(db, query)


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


def current_listing_prompt(db: Session, conv_id: str, *, include_native_forms: bool = True) -> str:
    import json
    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions or not isinstance(revisions[0].listing_json, dict):
        return ""
    listing = {key: value for key, value in revisions[0].listing_json.items() if key != "_native_forms"}
    contracts = revisions[0].listing_json.get("_native_forms")
    from vendoo_studio.services.native_contract import prompt

    native = prompt(contracts) if include_native_forms and isinstance(contracts, dict) else ""
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
        + native
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
    fingerprint = photo_fingerprint(photos)
    existing_analysis = latest_photo_analysis(history, fingerprint=fingerprint)
    if photos and existing_analysis and photo_analysis_usable(existing_analysis):
        photo_analysis_text = analysis_with_photo_count(len(photos), existing_analysis)
        skill_rules = load_skill_rules(
            f"{user_message_text}\n{photo_analysis_text}\n{seller_item_details(notes)}",
            db,
        )
    elif photos:
        provider = get_photo_provider()
        if provider:
            paths = [str(path) for path in photo_store().local_paths([p.stored_filename for p in photos])]
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
            repo.add_message(conv_id, "system", analysis_note, photo_fingerprint=fingerprint)
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
            "Never publish. Stop at saved drafts. Sending to Vendoo requires seller approval.\n"
            "Follow the canonical rules and the native form paths and choices below. "
            "Saved seller decisions and newer seller requests take precedence over historical messages.\n"
            + (f"\n{photo_analysis_text}\n\n" if photo_analysis_text else "") +
            comps_block +
            (f"\n--- Listing Rules ---\n\n{skill_rules}" if skill_rules else "")
        ) + current_listing_prompt(db, conv_id, include_native_forms=False) + learned_fields_prompt(db, conv_id),
    }

    from vendoo_studio.services.chat_field_context import chat_field_context

    system_prompt["content"] += chat_field_context(db, conv_id)
    from vendoo_studio.services.chat_forms import forms_prompt

    system_prompt["content"] += await forms_prompt(db, conv_id)
    from vendoo_studio.services.item_context import seller_messages

    system_prompt["content"] += item_decisions_prompt(db, conv_id)
    selected = {message.id for message in seller_messages(history, user_message_text)}
    # Assistant replies and save confirmations provide recent conversational
    # continuity; they cannot crowd old seller evidence out of its own budget.
    used = 0
    for message in reversed(history[-20:]):
        if message.role != "user" and used + len(message.text) <= 10000:
            selected.add(message.id)
            used += len(message.text)
    messages = [system_prompt]
    for msg in history:
        if msg.id not in selected:
            continue
        if photo_analysis_usable(msg.text) and getattr(msg, "photo_fingerprint", None) != fingerprint:
            continue
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
    "You are a product listing generator. Generate an evidence-backed listing from the supplied "
    "photo analysis, seller decisions, canonical listing rules, and native marketplace forms. "
    "The canonical skill defines the formulas and product-evidence requirements. "
    "Use each native form's supplied paths, choices, types and limits. "
    "Preserve verified category selections and server-owned metadata. "
    "Unsupported fields remain blockers; do not invent facts or interview the seller. "
    "Anteroom supplies saved package dimensions and inventory location. "
    "Generation saves a local draft only. Never publish or send without seller approval. "
    "For this application, return the JSON contract below, including for digital items.\n\n"
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
    native_forms: dict | None = None,
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
        f"{learned_fields_prompt(db, conv_id) if native_forms is None else ''}"
        "\n\n--- This item ---\n\n"
        f"{photo_line}"
        f"{item_details}\n\n"
        f"{analysis_text}"
        f"{comps_block}"
        f"{seller_history_prompt(db, conv_id, analysis_text)}"
        f"{item_decisions_prompt(db, conv_id)}"
        f"{current_listing_prompt(db, conv_id, include_native_forms=native_forms is None)}"
        f"{_empty_discovered_fields_prompt(db, conv_id, field_evidence) if native_forms is None else ''}"
        f"{_live_category_fields_prompt(db, conv_id) if native_forms is None else ''}"
        f"{_item_form_prompt(db, conv_id) if native_forms is None else ''}"
    )
    if native_forms is not None:
        from vendoo_studio.services.native_contract import prompt
        from vendoo_studio.services.form_schema import form_schema_prompt

        revisions = ListingRepo(db).get_revisions(conv_id)
        seed = revisions[0].listing_json if revisions else {}
        system_content += form_schema_prompt(seed, exclude=native_forms)

        system_content += prompt(native_forms)
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
