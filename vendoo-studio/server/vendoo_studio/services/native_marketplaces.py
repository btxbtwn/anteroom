"""Seller-operated native marketplace drafts. Publish is never queued or retried."""
from __future__ import annotations

from functools import cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vendoo_studio.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from vendoo_studio.services import browser_bridge, etsy_listings
from vendoo_studio.services.chrome_bridge import expected_extension_build
from vendoo_studio.services.api_job_lock import claim_api_job, release_api_job, start_gate
from vendoo_studio.services.vendoo_import import merge_notes, parse_notes


class NativeMarketplaceError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


Marketplace = Literal["poshmark", "mercari", "depop", "etsy", "sellwild"]
InspectionMarketplace = Literal["poshmark", "mercari", "depop", "etsy", "sellwild"]
# Marketplaces that list in one seller-confirmed step, with no marketplace draft.
ONE_STEP = frozenset({"poshmark"})
UNKNOWN = "The result is unconfirmed. Check the result before trying again; this action will not be retried."


class EtsyPackage(BaseModel):
    item_weight: float = Field(gt=0, allow_inf_nan=False)
    item_length: float = Field(gt=0, allow_inf_nan=False)
    item_width: float = Field(gt=0, allow_inf_nan=False)
    item_height: float = Field(gt=0, allow_inf_nan=False)
    item_weight_unit: Literal["oz", "lb", "g", "kg"]
    item_dimensions_unit: Literal["in", "cm", "mm", "m", "ft", "yd"]


class DraftRequest(BaseModel):
    revision_id: str
    shipping_profile_id: str | None = Field(default=None, pattern=r"^[1-9]\d*$")
    readiness_state_id: str | None = Field(default=None, pattern=r"^[1-9]\d*$")
    etsy_package: EtsyPackage | None = None
    sellwild_category_id: int | None = Field(default=None, ge=1)
    sellwild_condition_id: int | None = Field(default=None, ge=1)


class PublishRequest(DraftRequest):
    confirm: bool = False
    draft_url: str | None = None


class CheckRequest(BaseModel):
    draft_url: str | None = None


class InspectRequest(BaseModel):
    script_url: str | None = None
    background: bool = False


async def inspect_page(conv_id: str, marketplace: InspectionMarketplace, body: InspectRequest, db: Session):
    from types import SimpleNamespace

    _conversation(db, conv_id)
    if JobRepo(db).get_active():
        raise NativeMarketplaceError(409, "Wait for the current Chrome job to finish.")
    try:
        return await browser_bridge.request(SimpleNamespace(id=None), "marketplace.inspect", {
            "marketplace": marketplace, "script_url": body.script_url, "background": body.background,
        }, timeout=45)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, "Chrome could not inspect the marketplace page.") from exc


def draft_url(marketplace: str, value: str) -> str:
    """Accept only durable, native draft edit links, never arbitrary navigation."""
    import re

    parsed = urlparse(value)
    pattern, host = (
        (r"/your/shops/me/listing-editor/edit/\d+/?", "www.etsy.com") if marketplace == "etsy"
        else (r"/sellinghub/drafts/edit/[A-Za-z0-9_-]{1,80}/?", "www.depop.com") if marketplace == "depop"
        else (r"/sell/draft/[A-Za-z0-9_-]+/", "www.mercari.com")
    )
    if parsed.scheme != "https" or parsed.netloc != host or not re.fullmatch(pattern, parsed.path):
        raise NativeMarketplaceError(502, "Chrome did not return a valid saved draft link.")
    return f"https://{host}{parsed.path}"


def _conversation(db: Session, conv_id: str):
    conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise NativeMarketplaceError(404, "Conversation not found")
    return conv


def _bindings(conv) -> dict:
    value = parse_notes(conv.notes).get("nativeMarketplaceDrafts")
    return value if isinstance(value, dict) else {}


def _store(db: Session, conv, marketplace: str, binding: dict) -> None:
    db.refresh(conv)
    conv.notes = merge_notes(conv.notes, {"nativeMarketplaceDrafts": {**_bindings(conv), marketplace: binding}})
    db.commit()


def status(conv_id: str, db: Session):
    return _bindings(_conversation(db, conv_id))


def _apply_result(db: Session, conv, marketplace: str, binding: dict, result: dict) -> dict:
    db.refresh(conv)
    if _bindings(conv).get(marketplace, {}).get("job_id") != binding["job_id"]:
        raise NativeMarketplaceError(409, "A newer marketplace action has replaced this result.")
    action = binding["action"]
    reason = str(result.get("error") or "").strip()
    updated = {**binding, "state": "unknown", "error": f"{reason} {UNKNOWN}".strip()}
    # Retry only after a confirmed rejection or proof no write happened.
    if result.get("ok") is True:
        url = None if marketplace == "sellwild" or action == "list" else draft_url(marketplace, str(result.get("draft_url") or ""))
        if url and binding.get("draft_url") and url != binding["draft_url"]:
            raise NativeMarketplaceError(502, "Chrome answered for a different draft. Check the native listing.")
        expected = "published" if action in {"publish", "list"} else "draft"
        state = result.get("state")
        matches = state == expected or (marketplace == "sellwild" and action == "publish" and state == "pending_review")
        if matches:
            updated.update(state=state, error=None)
            if marketplace == "etsy":
                updated.update(etsy_pending_write=None, etsy_photos_complete=True)
            if url:
                updated["draft_url"] = url
            public = urlparse(str(result.get("listing_url") or ""))
            if public.scheme == "https" and public.netloc == {"poshmark": "poshmark.com", "mercari": "www.mercari.com", "depop": "www.depop.com", "etsy": "www.etsy.com", "sellwild": "sellwild.com"}[marketplace]:
                if marketplace == "sellwild":
                    import re
                    if not re.fullmatch(r"/product/\d+/[a-z0-9-]+", public.path):
                        raise NativeMarketplaceError(502, "Chrome returned an invalid Sellwild listing link.")
                updated["listing_url"] = f"https://{public.netloc}{public.path}"
            elif (marketplace == "sellwild" and action == "publish") or action == "list":
                raise NativeMarketplaceError(502, "Chrome did not return the listing link.")
    elif result.get("safe_to_retry") is True:
        recovered_url = binding.get("draft_url")
        if marketplace in {"depop", "etsy"} and action == "save_draft" and result.get("draft_url"):
            candidate = draft_url(marketplace, result["draft_url"])
            if recovered_url and candidate != recovered_url:
                raise NativeMarketplaceError(502, "Chrome answered for a different marketplace draft.")
            recovered_url = candidate
            updated["draft_url"] = candidate
        updated.update(state="draft" if recovered_url or marketplace == "sellwild" else "not_saved", error=str(result.get("error") or "Could not finish."))
        if action == "save_draft":
            updated["revision_id"] = binding.get("previous_revision_id")
    _store(db, conv, marketplace, updated)
    repo = JobRepo(db)
    step = f"native_{marketplace}_{'pending_review' if updated['state'] == 'pending_review' else action}"
    repo.update_status(binding["job_id"], "completed" if result.get("ok") and updated["state"] != "unknown" else "failed",
                       step, error=updated.get("error"))
    job = repo.get(binding["job_id"])
    if updated["state"] in {"draft", "published", "pending_review"} and result.get("ok") is True and job and job.status == "failed":
        job.status, job.last_error = "completed", None
        db.commit()
        repo.add_event(job.id, "native_result_verified", job.current_step)
    return updated


async def _run(conv_id: str, marketplace: str, body: DraftRequest, action: str, db: Session) -> dict:
    from vendoo_studio.routes.extension import extension_manager, schedule_advance_job_queue
    from vendoo_studio.services import activity
    from vendoo_studio.services.streaming import active_generation

    conv = _conversation(db, conv_id)
    listing = ListingRepo(db).get_current(conv_id)
    if not listing or listing.current_revision_id != body.revision_id:
        raise NativeMarketplaceError(409, "The listing changed. Refresh it before continuing.")
    revision = ListingRepo(db).get_revision(body.revision_id)
    snapshot = revision.listing_json
    if not isinstance(snapshot, dict) or not str(snapshot.get("title") or "").strip():
        raise NativeMarketplaceError(400, "Add a listing title first.")
    if active_generation(conv_id) or activity.running(conv_id):
        raise NativeMarketplaceError(409, "Wait for generation to finish.")
    if marketplace == "etsy":
        try:
            etsy_listings.connected_shop_id()
        except ValueError as exc:
            raise NativeMarketplaceError(409, str(exc)) from exc
    else:
        if not extension_manager.connected:
            raise NativeMarketplaceError(409, "Connect Chrome first.")
        if getattr(extension_manager, "build", None) != expected_extension_build():
            raise NativeMarketplaceError(409, "Connect Chrome to load the current Anteroom Bridge before continuing.")
    photos = ConversationRepo(db).get_photos(conv_id)
    with start_gate():
        db.refresh(conv)
        binding = _bindings(conv).get(marketplace, {})
        if binding.get("state") in {"saving", "publishing", "unknown"}:
            raise NativeMarketplaceError(409, UNKNOWN)
        if binding.get("state") in {"published", "pending_review"}:
            raise NativeMarketplaceError(409, "This draft has already been published.")
        if action == "list":
            if not isinstance(body, PublishRequest) or body.confirm is not True:
                raise NativeMarketplaceError(400, f"Confirm listing on {marketplace}.")
            if not photos:
                raise NativeMarketplaceError(400, "Add photos before listing.")
        elif action == "publish":
            if not isinstance(body, PublishRequest) or body.confirm is not True:
                raise NativeMarketplaceError(400, f"Confirm publishing to {marketplace}.")
            if (binding.get("state") != "draft" or binding.get("draft_url") != body.draft_url
                    or binding.get("revision_id") != body.revision_id):
                raise NativeMarketplaceError(409, "Save this revision as a draft and review it before publishing.")
        if action == "list":
            pass
        elif marketplace == "sellwild":
            if binding.get("state") != "draft" or binding.get("revision_id") != body.revision_id:
                raise NativeMarketplaceError(409, "Save this revision in Anteroom first.")
            if binding.get("photo_ids") != [p.id for p in photos]:
                raise NativeMarketplaceError(409, "The photos changed. Save this draft in Anteroom again.")
            if not photos:
                raise NativeMarketplaceError(400, "Add photos before publishing on Sellwild.")
            if not binding.get("sellwild_category_id") or not binding.get("sellwild_condition_id"):
                raise NativeMarketplaceError(409, "Choose a Sellwild category and condition, then save in Anteroom again.")
        elif not binding.get("draft_url") and not photos:
            raise NativeMarketplaceError(400, "Add at least one photo before saving a native draft.")
        repo = JobRepo(db)
        if repo.get_active():
            raise NativeMarketplaceError(409, "Another automation job is queued or running. Wait for it to finish.")
        job = repo.create(conv_id, body.revision_id, snapshot, status="dispatched", current_step=f"native_{marketplace}_{action}")
        claim_api_job(job.id)
        binding = {**binding, "state": "publishing" if action == "publish" else "saving", "action": action,
                   "previous_revision_id": binding.get("revision_id"), "revision_id": body.revision_id,
                   "job_id": job.id, "error": None}
        _store(db, conv, marketplace, binding)
    try:
        if marketplace == "etsy":
            from vendoo_studio.models.job import ACTIVE_JOB_STATUSES

            def record(values: dict) -> None:
                binding.update(values)
                _store(db, conv, marketplace, binding)

            def active() -> bool:
                db.refresh(job)
                return job.status in ACTIVE_JOB_STATUSES and _bindings(conv).get(marketplace, {}).get("job_id") == job.id

            from vendoo_studio.services import user_settings

            defaults = user_settings.get_etsy_listing_defaults()
            package = body.etsy_package.model_dump() if body.etsy_package else listing_package(snapshot)
            result = await etsy_listings.execute(action, snapshot, photos, binding, record, active,
                                                 confirm=isinstance(body, PublishRequest) and body.confirm is True,
                                                 shipping_profile_id=body.shipping_profile_id or defaults.get("shipping_profile_id"),
                                                 readiness_state_id=body.readiness_state_id or defaults.get("readiness_state_id"),
                                                 package=package)
            return _apply_result(db, conv, marketplace, binding, result)
        result = await browser_bridge.request(job, "marketplace.execute", {
            "marketplace": marketplace, "action": action, "confirm": action in {"publish", "list"},
            "operation_id": job.id, "draft_url": binding.get("draft_url"), "listing": snapshot,
            "local_draft_id": binding.get("local_draft_id"), "revision_id": body.revision_id,
            "photos": [{"id": p.id, "mime_type": p.mime_type} for p in photos],
            **({"sellwild": {"category_id": binding["sellwild_category_id"], "condition_id": binding["sellwild_condition_id"]}}
               if marketplace == "sellwild" else {}),
        }, timeout=180)
        return _apply_result(db, conv, marketplace, binding, result)
    except BaseException:
        # Chrome may already have committed the click. Never replay a mutation on disconnect.
        _store(db, conv, marketplace, {**binding, "state": "unknown", "error": UNKNOWN})
        JobRepo(db).update_status(job.id, "failed", job.current_step, error=UNKNOWN)
        raise
    finally:
        release_api_job(job.id)
        schedule_advance_job_queue()


async def save_draft(conv_id: str, marketplace: Marketplace, body: DraftRequest, db: Session):
    if marketplace in ONE_STEP:
        raise NativeMarketplaceError(409, f"{marketplace.title()} lists in one step. Use List.")
    if marketplace == "sellwild":
        return save_local_sellwild_draft(conv_id, body, db)
    try:
        return await _run(conv_id, marketplace, body, "save_draft", db)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, UNKNOWN) from exc


def listing_package(snapshot: dict) -> dict | None:
    """Etsy's packed weight (oz) and size (in) from the listing's own package fields."""
    import re

    try:
        ounces = float(snapshot.get("weight_lb") or 0) * 16 + float(snapshot.get("weight_oz") or 0)
    except (TypeError, ValueError):
        return None
    sides = [float(side) for side in re.findall(r"\d+(?:\.\d+)?", str(snapshot.get("package_dimensions_in") or ""))]
    if ounces <= 0 or len(sides) != 3 or min(sides) <= 0:
        return None
    return {"item_weight": round(ounces, 2), "item_length": sides[0], "item_width": sides[1], "item_height": sides[2],
            "item_weight_unit": "oz", "item_dimensions_unit": "in"}


@cache
def sellwild_catalog() -> dict:
    """Sellwild's own categories and conditions. Vendoo's Sellwild IDs are retired there."""
    import json

    from vendoo_studio.config import skills_dir

    return json.loads((skills_dir() / "list-this" / "references" / "sellwild-native-catalog.json").read_text(encoding="utf-8"))


# Marketplace and general category words that name the same Sellwild leaf.
_SELLWILD_SYNONYMS = {"tee": "shirt", "tshirt": "shirt", "tees": "shirt", "blouse": "top", "tank": "top", "cami": "top",
                      "jumper": "sweater", "sweatshirt": "sweater", "cardigan": "sweater", "pullover": "sweater",
                      "vest": "jacket", "gilet": "jacket", "parka": "coat", "trouser": "pant", "legging": "activewear",
                      "sneaker": "sneaker", "trainer": "sneaker", "bag": "handbag", "purse": "handbag"}


def _sellwild_words(text: str) -> set[str]:
    import re

    text = re.sub(r"\b(short|long|3/4)[ -]sleeves?\b", " ", text.lower()).replace("t-shirt", "tshirt")
    words = set()
    for word in re.findall(r"[a-z0-9]+", text):
        word = word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        words.add(_SELLWILD_SYNONYMS.get(word, word))
    return words


def _sellwild_text_sources(listing: dict) -> list[str]:
    """Where the listing already says what it is, most specific first."""
    import re

    poshmark = ((listing.get("marketplace_category_objects") or {}).get("poshmark") or {}).get("displayPath") or []
    depop = str((listing.get("marketplace_category_ids") or {}).get("depop") or "")
    # eBay's top level names every clothing branch; it says nothing about the item.
    general = re.sub(r"clothing,? shoes (&|and) accessories", " ", str(listing.get("category_path") or ""), flags=re.I)
    return [" ".join(map(str, poshmark[1:])), depop.split("_", 1)[-1].replace("-", " "),
            " ".join(general.replace(">", "/").split("/")[-2:]), str(listing.get("title") or "")]


def suggest_sellwild(listing: dict) -> dict:
    """Sellwild's own category and condition, worked out from what the listing already says."""
    import re

    catalog = sellwild_catalog()
    ids = {option["id"] for option in catalog["categories"]}
    specifics = listing.get("sellwild_specifics") if isinstance(listing.get("sellwild_specifics"), dict) else {}
    explicit = specifics.get("nativeCategoryId")
    category_id = int(explicit) if str(explicit or "").isdigit() and int(explicit) in ids else None
    poshmark = ((listing.get("marketplace_category_objects") or {}).get("poshmark") or {}).get("displayPath") or []
    everything = " ".join([str(listing.get("category_path") or ""), " ".join(map(str, poshmark)),
                           str((listing.get("marketplace_category_ids") or {}).get("depop") or "")]).lower()
    if category_id is None and re.search(r"\bbaby\b|infant|newborn", everything):
        category_id = 66
    elif category_id is None and re.search(r"\bkids?\b|kidswear|\bboys?\b|\bgirls?\b", everything):
        category_id = 246
    # Vendoo's (retired) Sellwild category still names the right top-level group.
    vendoo_group = str((((listing.get("marketplace_category_objects") or {}).get("sellwild") or {}).get("displayPath") or [""])[0] or "")
    clothing = bool(re.search(r"clothing|menswear|womenswear|\bmen\b|\bwomen\b|shoes|accessories", everything))
    group = vendoo_group or ("Fashion" if clothing else "")
    if category_id is None:
        for text in _sellwild_text_sources(listing):
            words = _sellwild_words(text)
            best, score = None, 0
            for option in catalog["categories"]:
                parent, _, leaf = option["label"].partition(" > ")
                if group and parent != group:
                    continue
                hits = len(_sellwild_words(leaf.replace("&", " ").replace(",", " ")) & words)
                if hits > score:
                    best, score = option["id"], hits
            if best is not None:
                category_id = best
                break
    native_condition = specifics.get("nativeConditionId")
    condition = str(listing.get("condition") or "").lower()
    condition_id = (int(native_condition) if str(native_condition or "") in {"1", "2", "3", "4", "5"}
                    else 1 if re.search(r"poor|major flaw", condition)
                    else 4 if re.search(r"new without tags|nwot|like new", condition)
                    else 3 if re.search(r"new with defects|imperfection", condition)
                    else 5 if re.search(r"new with tags|nwt|brand new|^new\b", condition)
                    else 2 if re.search(r"pre-owned|used|good|fair|excellent", condition) else None)
    return {"category_id": category_id, "condition_id": condition_id}


def sellwild_options(conv_id: str, db: Session) -> dict:
    _conversation(db, conv_id)
    listing = ListingRepo(db).get_current(conv_id)
    revision = ListingRepo(db).get_revision(listing.current_revision_id) if listing else None
    snapshot = revision.listing_json if revision and isinstance(revision.listing_json, dict) else {}
    catalog = sellwild_catalog()
    return {"categories": catalog["categories"], "conditions": catalog["conditions"], "suggested": suggest_sellwild(snapshot)}


def save_local_sellwild_draft(conv_id: str, body: DraftRequest, db: Session):
    import uuid

    conv = _conversation(db, conv_id)
    listing = ListingRepo(db).get_current(conv_id)
    if not listing or listing.current_revision_id != body.revision_id:
        raise NativeMarketplaceError(409, "The listing changed. Refresh it before continuing.")
    snapshot = ListingRepo(db).get_revision(body.revision_id).listing_json
    if not isinstance(snapshot, dict) or not str(snapshot.get("title") or "").strip():
        raise NativeMarketplaceError(400, "Add a listing title first.")
    catalog = sellwild_catalog()
    automatic = suggest_sellwild(snapshot)
    body = body.model_copy(update={"sellwild_category_id": body.sellwild_category_id or automatic["category_id"],
                                   "sellwild_condition_id": body.sellwild_condition_id or automatic["condition_id"]})
    if body.sellwild_category_id not in {option["id"] for option in catalog["categories"]}:
        raise NativeMarketplaceError(400, "Anteroom couldn't match a Sellwild category for this item. Choose one with Change.")
    if body.sellwild_condition_id not in {option["id"] for option in catalog["conditions"]}:
        raise NativeMarketplaceError(400, "Anteroom couldn't match a Sellwild condition for this item. Choose one with Change.")
    with start_gate():
        db.refresh(conv)
        previous = _bindings(conv).get("sellwild", {})
        if previous.get("state") in {"saving", "publishing", "unknown", "published", "pending_review"}:
            raise NativeMarketplaceError(409, "Check the previous Sellwild action before saving another draft.")
        binding = {"storage": "local", "state": "draft", "revision_id": body.revision_id,
                   "photo_ids": [p.id for p in ConversationRepo(db).get_photos(conv_id)],
                   "local_draft_id": previous.get("local_draft_id") or uuid.uuid4().hex,
                   "sellwild_category_id": body.sellwild_category_id,
                   "sellwild_condition_id": body.sellwild_condition_id, "error": None}
        _store(db, conv, "sellwild", binding)
    return binding


async def list_now(conv_id: str, marketplace: Marketplace, body: PublishRequest, db: Session):
    """Create a live listing in one seller-confirmed step. Never queued or retried."""
    if marketplace not in ONE_STEP:
        raise NativeMarketplaceError(409, f"{marketplace.title()} does not list in one step yet. Save a draft first.")
    if body.confirm is not True:
        raise NativeMarketplaceError(400, f"Confirm listing on {marketplace}.")
    try:
        return await _run(conv_id, marketplace, body, "list", db)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, UNKNOWN) from exc


async def publish(conv_id: str, marketplace: Marketplace, body: PublishRequest, db: Session):
    if marketplace in ONE_STEP:
        raise NativeMarketplaceError(409, f"{marketplace.title()} lists in one step. Use List.")
    if body.confirm is not True:
        raise NativeMarketplaceError(400, f"Confirm publishing to {marketplace}.")
    try:
        return await _run(conv_id, marketplace, body, "publish", db)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, UNKNOWN) from exc


async def check_result(conv_id: str, marketplace: Marketplace, db: Session, body: CheckRequest | None = None):
    from types import SimpleNamespace

    conv = _conversation(db, conv_id)
    binding = _bindings(conv).get(marketplace, {})
    if not binding.get("job_id"):
        raise NativeMarketplaceError(409, "No native marketplace action to check.")
    job = JobRepo(db).get(binding["job_id"])
    snapshot = job.listing_snapshot if job else {}
    candidate = draft_url(marketplace, body.draft_url) if body and body.draft_url else binding.get("draft_url")
    if binding.get("draft_url") and candidate != binding["draft_url"]:
        raise NativeMarketplaceError(409, "Check the draft already linked to this listing.")
    if marketplace == "etsy":
        from vendoo_studio.services.api_job_lock import api_job_is_live

        if api_job_is_live(binding["job_id"]):
            raise NativeMarketplaceError(409, "The Etsy API action is still running. Wait for it to finish.")
        result = await etsy_listings.check(snapshot, binding, candidate)
        return _apply_result(db, conv, marketplace, binding, result)
    try:
        result = await browser_bridge.request(SimpleNamespace(id=None), "marketplace.result", {
            "operation_id": binding["job_id"],
            "context": {"marketplace": marketplace, "action": binding["action"],
                        "operation_id": binding["job_id"], "draft_url": candidate,
                        "local_draft_id": binding.get("local_draft_id"), "revision_id": binding.get("revision_id"),
                        "photo_ids": binding.get("photo_ids"),
                        "expected_photo_count": len(ConversationRepo(db).get_photos(conv_id)) if binding["action"] == "save_draft" and not binding.get("previous_revision_id") else None,
                        "listing": {key: snapshot.get(key) for key in ("title", "description", "price")}},
        }, timeout=65)
    except browser_bridge.BrowserBridgeError as exc:
        raise NativeMarketplaceError(502, "Chrome could not confirm the result. Check the native tab before trying again.") from exc
    if not result.get("finished"):
        raise NativeMarketplaceError(409, "Chrome has no confirmed result yet. Check the native tab; do not repeat the action.")
    return _apply_result(db, conv, marketplace, binding, result["result"])
