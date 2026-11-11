"""Save listing changes that come back from the chat assistant."""

from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from vendoo_studio.services import listing_persistence
from vendoo_studio.repositories.queries import ConversationRepo, ListingRepo
from vendoo_studio.services.listing_generate import (
    extract_listing_json,
    listing_save_summary,
    looks_like_listing_attempt,
    repair_listing_json,
)
from vendoo_studio.services.listing_patch import apply_json_patch, extract_json_patch
from vendoo_studio.services.registry import align_listing_gender


def _sync_category_override(db: Session, conv_id: str, listing: dict) -> None:
    from vendoo_studio.services.vendoo_import import merge_notes, parse_notes

    category = str((listing or {}).get("category_path") or "").strip()
    if not category:
        return
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        return
    current = str(parse_notes(conv.notes).get("categoryOverride") or "").strip()
    if not current or current == category:
        return
    conv.notes = merge_notes(conv.notes, {"categoryOverride": category})
    db.commit()


def _seller_asked_for_sku(user_message: str) -> bool:
    """The seller typed a request about the SKU. Ask chat's generated fill
    prompt does not count: it asks the model to make values up."""
    import re

    from vendoo_studio.services.fill_log import is_missing_fields_request

    return bool(re.search(r"\bskus?\b", user_message or "", re.IGNORECASE)) and not is_missing_fields_request(
        user_message)


def _sku_key(path: object, separator: str) -> str | None:
    """The SKU key a path names directly: "sku" or "depop_specifics", else None."""
    parts = str(path or "").strip(separator).split(separator)
    if parts == ["sku"]:
        return "sku"
    if len(parts) == 2 and parts[0].endswith("_specifics") and parts[1] == "sku":
        return parts[0]
    return None


def _patched_sku_keys(operations: list[dict]) -> set[str]:
    """The SKU keys a JSON Patch names directly, like /sku or /depop_specifics/sku."""
    keys = {_sku_key(operation.get("path"), "/") for operation in operations if isinstance(operation, dict)}
    return {key for key in keys if key}


def save_listing_revision(
    db: Session, conv_id: str, listing: dict, *, operations: list[dict] | None = None,
    sku_keys: set[str] | frozenset[str] = frozenset(),
) -> dict:
    """Save chat output. ``sku_keys`` names the SKUs ("sku", "depop_specifics")
    the seller's request set field by field; every other SKU stays the seller's."""
    from vendoo_studio.services.validation import normalize_listing_dropdowns
    from vendoo_studio.services.listing_generate import align_size_fields, clear_not_applicable_values

    updated = align_listing_gender(listing, operations)
    stamped = _stamp_learned_fields(db, updated)
    revisions = ListingRepo(db).get_revisions(conv_id)
    previous = revisions[0].listing_json if revisions else {}
    # Chat may rewrite copy, but SKUs belong to the seller.
    for key in {"sku", *(key for key in stamped.keys() | previous.keys() if key.endswith("_specifics"))}:
        if key in sku_keys:
            continue
        if key == "sku":
            if key in previous:
                stamped[key] = previous[key]
            else:
                stamped.pop(key, None)
        else:
            seller_block = previous.get(key) or {}
            if isinstance(seller_block, dict) and "sku" in seller_block:
                if not isinstance(stamped.get(key), dict):
                    stamped[key] = {}
                stamped[key]["sku"] = seller_block["sku"]
            elif isinstance(stamped.get(key), dict):
                stamped[key].pop("sku", None)
    previous_size = None
    if revisions and isinstance(revisions[0].listing_json, dict):
        previous_size = revisions[0].listing_json.get("size")
    from vendoo_studio.services.seller_overrides import preserving_locks

    def _normalize(candidate: dict) -> None:
        clear_not_applicable_values(candidate)
        align_size_fields(candidate, previous_size=None if previous_size is None else str(previous_size))
        normalize_listing_dropdowns(candidate)

    preserving_locks(stamped, _normalize)
    parent_id = revisions[0].id if revisions else None
    revision = listing_persistence.save_revision(db,
        conv_id,
        stamped,
        source="model_refinement",
        parent_revision_id=parent_id,
    )
    _sync_category_override(db, conv_id, revision.listing_json)
    return revision.listing_json


def _stamp_learned_fields(db: Session, listing: dict) -> dict:
    from vendoo_studio.services.registry import RegistryService

    if isinstance(listing, dict):
        RegistryService(db).merge_learned_fields(listing)
    return listing


def _save_missing_fields(db: Session, conv_id: str, missing_fields: list[dict], *, user_message: str = "") -> bool:
    from vendoo_studio.services.fill_log import (
        FillLogService,
        field_lookup_key,
        listing_value_for_field,
        summarize_missing_fields,
        remember_chat_clears,
        write_values_into_listing,
    )

    revisions = ListingRepo(db).get_revisions(conv_id)
    if not revisions:
        ConversationRepo(db).add_message(
            conv_id,
            "system",
            "Could not save field values — generate a listing first, then Ask chat again.",
            provider="system",
            model="",
        )
        return False
    from vendoo_studio.services.validation import normalize_listing_dropdowns, validate_listing
    from vendoo_studio.services.listing_generate import align_size_fields

    updated = copy.deepcopy(revisions[0].listing_json)
    accepted = []
    rejected = []
    for field in missing_fields:
        before = validate_listing(copy.deepcopy(updated), require_photos=False)
        candidate = write_values_into_listing(copy.deepcopy(updated), [field])
        after = validate_listing(candidate, require_photos=False)
        previous_errors = {(error["field"], error["message"]) for error in before.errors}
        new_errors = [error for error in after.errors if (error["field"], error["message"]) not in previous_errors]
        if field.get("value") in ("", []) and new_errors:
            rejected.extend(error["message"] for error in new_errors)
            continue
        updated = candidate
        accepted.append(field)
    from vendoo_studio.services.seller_overrides import preserving_locks

    def _normalize(candidate: dict) -> None:
        align_size_fields(candidate)
        normalize_listing_dropdowns(candidate)

    preserving_locks(updated, _normalize)
    # Report the saved value when normalization restored a cleared required
    # field or completed a size-first title.
    for index, field in enumerate(accepted):
        if field.get("value") in ("", []) or field.get("field", "").casefold() == "title":
            value = listing_value_for_field(updated, field.get("marketplace", "general"), field["field"])
            if value:
                accepted[index] = {**field, "value": value}
    updated = remember_chat_clears(updated, accepted)
    sku_keys = {
        "sku" if field.get("marketplace", "general") in {"", "general", "unknown"} else f"{field['marketplace']}_specifics"
        for field in accepted if field_lookup_key(field.get("field") or "") == "sku"
    } if _seller_asked_for_sku(user_message) else set()
    save_listing_revision(db, conv_id, updated, sku_keys=sku_keys)
    FillLogService(db).record_generated_values(conv_id, accepted)

    ConversationRepo(db).add_message(
        conv_id,
        "system",
        (summarize_missing_fields(accepted) if accepted else "No proposed field changes were accepted.")
        + ("\nKept existing values because the proposed changes would introduce validation errors:\n"
           + "\n".join(f"- {message}" for message in dict.fromkeys(rejected)) if rejected else ""),
        provider="system",
        model="",
    )
    return True


def recover_saved_chat_clears(db: Session, conv_id: str, revision) -> dict:
    """Recover explicit clears from the chat reply that produced a saved revision."""
    from vendoo_studio.models.conversation import Message
    from vendoo_studio.services.fill_log import (
        _is_clear,
        extract_missing_fields,
        listing_value_for_field,
        remember_chat_clears,
    )

    listing = dict(revision.listing_json or {})
    if revision.source != "model_refinement" or "reviewed_empty_fields" in listing:
        return listing
    query = db.query(Message).filter(
        Message.conversation_id == conv_id,
        Message.role == "assistant",
        Message.created_at <= revision.created_at,
    )
    parent = ListingRepo(db).get_revision(revision.parent_revision_id) if revision.parent_revision_id else None
    if parent:
        query = query.filter(Message.created_at > parent.created_at)
    message = query.order_by(Message.created_at.desc()).first()
    patches = extract_missing_fields(message.text) if message else None
    clears = [patch for patch in patches or [] if _is_clear(patch.get("value")) and not
              listing_value_for_field(listing, patch["marketplace"], patch["field"])]
    return remember_chat_clears(listing, clears)


def _field_commands(text: str) -> list[dict] | None:
    import json
    import re

    if not text:
        return None
    candidates = []
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        rows = parsed.get("fields") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            continue
        cleaned = [row for row in rows if isinstance(row, dict) and str(row.get("path") or "").strip()]
        if cleaned:
            return cleaned
    return None


def _reject_best_offer(db: Session, conv_id: str, listing: dict) -> bool:
    from vendoo_studio.services.vendoo_marketplace_fields import best_offer_error

    problem = best_offer_error(listing)
    if not problem:
        return False
    ConversationRepo(db).add_message(conv_id, "system", problem, provider="system", model="")
    return True


def apply_listing_payload(
    db: Session, conv_id: str, full_text: str, *, user_message: str = "",
) -> tuple[list[dict] | None, bool]:
    from fastapi import HTTPException

    from vendoo_studio.services.fill_log import extract_missing_fields
    from vendoo_studio.services.seller_overrides import apply_field_updates

    commands = _field_commands(full_text)
    if commands and not _seller_asked_for_sku(user_message):
        # SKUs belong to the seller: chat sets one only when asked to.
        commands = [command for command in commands if not _sku_key(command["path"], ".")]
        if not commands:
            ConversationRepo(db).add_message(
                conv_id, "system", "No proposed field changes were accepted.", provider="system", model="",
            )
            return None, True
    if commands:
        revisions = ListingRepo(db).get_revisions(conv_id)
        if not revisions or not isinstance(revisions[0].listing_json, dict):
            return None, False
        updated = copy.deepcopy(revisions[0].listing_json)
        apply_field_updates(updated, commands)
        from vendoo_studio.services.listing_editor import save_listing_edit
        from vendoo_studio.repositories.queries import RevisionConflict

        try:
            save_listing_edit(conv_id, updated, db, expected_revision_id=revisions[0].id)
        except RevisionConflict as exc:
            ConversationRepo(db).add_message(conv_id, "system", str(exc), provider="system", model="")
            return commands, False
        except HTTPException as exc:
            ConversationRepo(db).add_message(conv_id, "system", str(exc.detail), provider="system", model="")
            return commands, False
        ConversationRepo(db).add_message(
            conv_id, "system", "Saved those field changes to the listing.", provider="system", model="",
        )
        return commands, True

    missing_fields = extract_missing_fields(full_text)
    if missing_fields:
        return None, _save_missing_fields(db, conv_id, missing_fields, user_message=user_message)

    parsed_ops = extract_json_patch(full_text)
    if parsed_ops:
        lr = ListingRepo(db)
        revisions = lr.get_revisions(conv_id)
        if not revisions:
            return None, False
        updated = apply_json_patch(dict(revisions[0].listing_json), parsed_ops)
        if _reject_best_offer(db, conv_id, updated):
            return parsed_ops, False
        sku_keys = _patched_sku_keys(parsed_ops) if _seller_asked_for_sku(user_message) else set()
        save_listing_revision(db, conv_id, updated, operations=parsed_ops, sku_keys=sku_keys)
        ConversationRepo(db).add_message(
            conv_id,
            "system",
            "Saved those changes to the listing.",
            provider="system",
            model="",
        )
        return parsed_ops, True

    parsed = extract_listing_json(full_text)
    if parsed:
        if _reject_best_offer(db, conv_id, parsed):
            return None, False
        saved = save_listing_revision(db, conv_id, parsed)
        ConversationRepo(db).add_message(
            conv_id,
            "system",
            listing_save_summary(db, conv_id, saved),
            provider="system",
            model="",
        )
        return None, True
    return None, False


async def apply_listing_payload_with_repair(
    db: Session,
    conv_id: str,
    full_text: str,
    provider,
    *,
    user_message: str = "",
) -> tuple[list[dict] | None, bool]:
    from vendoo_studio.services.fill_log import (
        is_missing_fields_request,
        looks_like_missing_fields_attempt,
        repair_missing_fields,
    )

    operations, saved = apply_listing_payload(db, conv_id, full_text, user_message=user_message)
    if saved:
        return operations, True

    ask_fields = is_missing_fields_request(user_message)
    if ask_fields or looks_like_missing_fields_attempt(full_text):
        repaired_fields = await repair_missing_fields(provider, full_text, user_message)
        if repaired_fields and _save_missing_fields(db, conv_id, repaired_fields, user_message=user_message):
            return None, True
        if ask_fields:
            ConversationRepo(db).add_message(
                conv_id,
                "system",
                "Could not save field values into the listing JSON. Retry Ask chat.",
                provider="system",
                model="",
            )
        return None, False

    if extract_listing_json(full_text):
        return None, False
    if extract_json_patch(full_text):
        return None, False
    if not looks_like_listing_attempt(full_text):
        return None, False

    repaired = await repair_listing_json(provider, full_text)
    if not repaired:
        return None, False
    save_listing_revision(db, conv_id, repaired)
    ConversationRepo(db).add_message(
        conv_id,
        "system",
        "Repaired malformed listing JSON and saved it for review.",
        provider="system",
        model="",
    )
    return None, True


async def persist_chat_result(
    db: Session,
    conv_id: str,
    full_text: str,
    provider,
    *,
    provider_name: str,
    provider_model: str,
    stream_error: str,
    user_message: str,
) -> tuple[list[dict] | None, bool]:
    stream_repo = ConversationRepo(db)
    usable = full_text.strip() and not full_text.lstrip().lower().startswith("error:")
    saved = False
    operations: list[dict] | None = None
    if usable and not stream_error:
        stream_repo.add_message(conv_id, "assistant", full_text, provider=provider_name, model=provider_model)
        operations, saved = await apply_listing_payload_with_repair(
            db, conv_id, full_text, provider, user_message=user_message
        )
        from vendoo_studio.services import activity

        scope = activity.edit_fields.get()
        if saved and (scope is None or any("categor" in path.casefold() for path in scope)):
            from vendoo_studio.services.agent_categories import apply_saved_agent_category

            await apply_saved_agent_category(db, conv_id, operations=operations, source_text=full_text)
    elif stream_error or not full_text.strip():
        stream_repo.add_message(
            conv_id,
            "system",
            stream_error or "The listing assistant returned an empty response. Retry this prompt.",
            provider="system",
            model="",
        )

    from vendoo_studio.repositories.queries import (
        LISTING_STATUSES,
        JobRepo,
    )
    from vendoo_studio.services.vendoo_import import parse_notes

    active = any(job.conversation_id == conv_id for job in JobRepo(db).get_active())
    if active:
        stream_repo.update_status(conv_id, "listing")
    else:
        # Prefer the imported inventory label so an already-listed item does
        # not drop to draft after a chat fill.
        conv = stream_repo.get(conv_id)
        imported_status = str(parse_notes(conv.notes if conv else None).get("vendooStatus") or "")
        if imported_status not in LISTING_STATUSES:
            imported_status = "draft"
        stream_repo.update_status(conv_id, imported_status)
    return operations, saved
