"""A second opinion on model-owned garment choices at the end of draft generation."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import httpx

from anteroom.database import in_worker_session
from anteroom.repositories.queries import ConversationRepo, RevisionConflict
from anteroom.services import api_usage, live_trace, model_usage, native_contract, native_field_fill, user_settings
from anteroom.services.hosted_provider import managed_openrouter_key
from anteroom.services.keychain import get_ai_key
from anteroom.services.listing_generate import notes_fingerprint
from anteroom.services.listing_persistence import save_revision
from anteroom.services.seller_overrides import field_locked, fold_key, get_path, locked_paths
from anteroom.services.service_context import current
from anteroom.services.shared_http import http_client

log = logging.getLogger(__name__)
MODEL = "typesafe/jev-1.14"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MIN_PROBABILITY = 0.90
UNKNOWN = "Insufficient evidence"
GARMENT_FIELDS = {"closure", "fastening", "pattern", "sleevelength", "material", "outershellmaterial",
                  "fabrictype", "color", "colour"}


@dataclass
class Field:
    path: str
    label: str
    original: str
    choices: dict[str, str]
    quote: str


def _fields(listing: dict, evidence: str) -> list[Field]:
    proofs = listing.get(native_contract.EVIDENCE_KEY)
    contracts = listing.get(native_contract.KEY)
    if not isinstance(proofs, dict) or not proofs or not isinstance(contracts, dict):
        return []
    locks = locked_paths(listing)
    fields = []
    for market, contract in contracts.items():
        if (market not in native_field_fill.NAMES or not isinstance(contract, dict)
                or not any(isinstance(path, str) and path.startswith(f"{market}_specifics.") for path in proofs)):
            continue
        form = native_contract.editor_form(listing, market)
        if not form:
            continue
        for row in form["fields"]:
            path = native_contract.path_of(row)
            label = str(row.get("label") or "")
            if fold_key(label) not in GARMENT_FIELDS or not native_contract.item_field(row, market):
                continue
            if row.get("multi") or row.get("clears") or path in (contract.get("inputs") or {}):
                continue
            if field_locked(listing, market, path.rsplit(".", 1)[-1]) or any(
                path == lock or path.startswith(lock + ".") for lock in locks
            ):
                continue
            if fold_key(label) in {"color", "colour"} and locks.intersection({"primaryColor", "secondaryColor"}):
                continue
            value = get_path(listing, path)
            proof = proofs.get(path)
            if not isinstance(proof, dict):
                continue
            quote = proof.get("quote")
            if not isinstance(value, str) or not value or proof.get("value") != value:
                continue
            if not isinstance(quote, str) or len(quote.strip()) < 3 or quote not in evidence:
                continue
            row = native_contract.applicable_row(row, listing, form["fields"])
            ids = row.get("option_values") or {}
            choices = {option: str(ids.get(option, option)) for option in row.get("options") or []
                       if isinstance(option, str) and option}
            original = next((k for k, v in choices.items() if value in (k, v)), None)
            if original and len(choices) > 1 and UNKNOWN not in choices:
                fields.append(Field(path, label, original, choices, quote))
    return fields


def _questions(fields: list[Field]) -> dict:
    questions = {}
    for i, field in enumerate(fields):
        context = json.dumps({"field": field.label, "marketplace_field": field.path, "source_quote": field.quote})
        questions[f"field{i}"] = {
            "type": "choice",
            "instructions": (f"Which single option does this source quote explicitly support? {context} "
                             "Distinguish shell from lining. Choose Insufficient evidence if several options "
                             "fit, sources conflict or the quote is unrelated."),
            "criteria": {**dict.fromkeys(field.choices), UNKNOWN: "No unique supported answer."},
        }
        questions[f"wrong{i}"] = {
            "type": "noul",
            "instructions": (f"Does the quote explicitly contradict the proposed value {json.dumps(field.original)} "
                             f"for this exact field? {context} Missing information, synonyms, blends and other "
                             "possible interpretations are not contradictions."),
        }
    return questions


def _key() -> tuple[str | None, bool]:
    from anteroom import sandbox
    if sandbox.enabled():
        return None, False
    managed = managed_openrouter_key() if current() is not None else ""
    return (managed, True) if managed else (get_ai_key("openrouter"), False)


async def _decide(conv_id: str, evidence: str, fields: list[Field]) -> dict:
    key, managed = await asyncio.to_thread(_key)
    if not key:
        raise ConnectionError("OpenRouter is not connected")
    with model_usage.observe("openrouter", MODEL) as call:
        async with http_client() as client:
            response = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {key}"},
                                         json={"model": MODEL, "state": evidence, "questions": _questions(fields)},
                                         timeout=15)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid Jev response")
        if isinstance(payload.get("usage"), dict):
            call.usage(payload["usage"])
        if managed:
            await asyncio.to_thread(api_usage.record_openrouter, model=MODEL, kind="field_review",
                                    cost_usd=call.record["cost_usd"], conversation_id=conv_id,
                                    provider_request_id=payload.get("id"), input_tokens=call.record["input_tokens"],
                                    output_tokens=call.record["output_tokens"])
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("Missing Jev decisions")
        return answers


def _confident(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and MIN_PROBABILITY <= value <= 1


def _changes(fields: list[Field], answers: dict) -> list[tuple[Field, str]]:
    changes = []
    for i, field in enumerate(fields):
        choice, contradiction = answers.get(f"field{i}"), answers.get(f"wrong{i}")
        if not isinstance(choice, dict) or not isinstance(contradiction, dict):
            continue
        replacement = choice.get("choice")
        probabilities = choice.get("probabilities")
        if (choice.get("type") == "choice" and contradiction.get("type") == "noul"
                and isinstance(replacement, str) and replacement in field.choices
                and fold_key(replacement) not in {"other", "unknown", "notspecified", "doesnotapply", "na"}
                and field.choices[replacement] != field.choices[field.original]
                and isinstance(probabilities, dict) and _confident(probabilities.get(replacement))
                and _confident(contradiction.get("noul"))):
            changes.append((field, replacement))
    return changes


def _snapshot(db, conv_id, photos, notes, expected_listing=None):
    conv = ConversationRepo(db).get(conv_id)
    snapshot = native_field_fill.fill_inputs(db, conv_id)
    if not conv or notes_fingerprint(conv.notes) != notes or snapshot is None or snapshot[0] != photos:
        raise RevisionConflict("Draft evidence changed during Jev review")
    if expected_listing is not None and snapshot[2] != expected_listing:
        raise RevisionConflict("Draft changed before Jev review")
    return snapshot


def _finish_review(db, conv_id, photos, expected_notes, revision_id, changes, failure=None):
    snapshot = _snapshot(db, conv_id, photos, expected_notes)
    if snapshot[1] != revision_id:
        raise RevisionConflict("Draft changed during Jev review")
    if not user_settings.jev_review_enabled():
        return None
    if failure:
        ConversationRepo(db).add_message(conv_id, "system", failure, provider="openrouter", model=MODEL)
        return None
    if not changes:
        return None
    updated = native_contract.copy_listing(snapshot[2])
    notes = []
    for field, replacement in changes:
        value = field.choices[replacement]
        native_field_fill.write_value(updated, field.path, value)
        updated[native_contract.EVIDENCE_KEY][field.path] = {"value": value, "quote": field.quote}
        market = field.path.split("_specifics.", 1)[0]
        notes.append(f"{native_field_fill.NAMES[market]} {field.label}: {field.original} → {replacement} "
                     f"(evidence: “{field.quote}”)")
    # This is a patch to a saved revision. Re-normalizing the entire draft here
    # could change an unrelated answer that this pass never evaluated.
    saved = save_revision(db, conv_id, updated, source="jev_review", expected_revision_id=revision_id,
                          parent_revision_id=revision_id, commit=False, verbatim=True)
    result = saved.listing_json
    ConversationRepo(db).add_message(conv_id, "system", "Jev corrected draft fields:\n" + "\n".join(notes),
                                     provider="openrouter", model=MODEL)
    return result


async def review_generation(conv_id: str, evidence: str, photos: str, listing: dict, notes: str) -> dict | None:
    """Return the corrected listing, or None when nothing was saved.

    A seller change during the review wins: the stale correction is dropped and
    generation finishes as it would without Jev (a photo change still
    supersedes it through the generation finish's own photo checks).
    """
    try:
        return await _review(conv_id, evidence, photos, listing, notes)
    except RevisionConflict as exc:
        log.info("Jev review skipped for %s: %s", conv_id, exc)
        return None


async def _review(conv_id: str, evidence: str, photos: str, listing: dict, notes: str) -> dict | None:
    if not evidence or not listing.get(native_contract.EVIDENCE_KEY):
        return None
    if not await asyncio.to_thread(user_settings.jev_review_enabled):
        return None
    fields = await asyncio.to_thread(_fields, listing, evidence)
    if not fields:
        return None
    snapshot = await in_worker_session(_snapshot, conv_id, photos, notes, listing)
    async with live_trace.agent("jev-review", "Jev field review") as agent:
        try:
            async with asyncio.timeout(20):
                answers = await _decide(conv_id, evidence, fields)
        except (ValueError, TimeoutError, httpx.HTTPError, RuntimeError, OSError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            log.info("Jev review unavailable (%s)", status or type(exc).__name__)
            message = "Jev could not check these fields. The original draft was saved."
            if status == 402:
                message += " OpenRouter has insufficient credit."
            elif isinstance(exc, ConnectionError):
                message += " Connect OpenRouter in Settings to use Jev."
            elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                message += " OpenRouter timed out."
            elif status in {401, 403}:
                message += " OpenRouter rejected the connection."
            agent.result(message)
            return await in_worker_session(_finish_review, conv_id, photos, notes, snapshot[1], [], message)
        changes = _changes(fields, answers)
        result = await in_worker_session(_finish_review, conv_id, photos, notes, snapshot[1], changes)
        agent.result(f"Corrected {len(changes)} field(s)" if result else "No changes saved")
        return result
