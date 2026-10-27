"""Import active Poshmark, Mercari, Depop, Sellwild and Vinted listings through Chrome.

The extension reads the seller's own listings with the seller's signed-in
sessions and checks each one belongs to that account. Nothing here writes to a
marketplace: it previews a page of listings, names the local item that already
owns each one, suggests a likely match for the seller to review, and saves new
local items through the same path as the eBay and Etsy imports.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import math
import re
from types import SimpleNamespace

from sqlalchemy.orm import Session

from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.schema import VALID_CONDITIONS, ListingSchema
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import browser_bridge, inventory_import
from vendoo_studio.services.chrome_sales import _LISTING_ID
from vendoo_studio.services.marketplace_identity import listing_reference, public_listing_url

MARKETPLACES = ("poshmark", "mercari", "depop", "sellwild", "vinted")
MATCH_THRESHOLD = 0.72
MAX_ROWS = 100
_NAMES = {"poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop", "sellwild": "Sellwild", "vinted": "Vinted"}

# Each marketplace's own condition, in Anteroom's words.
_CONDITIONS = {
    "poshmark": {"nwt": "New With Tags/Box", "ret": "New With Tags/Box", "uln": "Pre-Owned - Excellent",
                 "ug": "Pre-Owned - Good", "uf": "Pre-Owned - Fair", "not_nwt": "Pre-Owned - Good"},
    "mercari": {"new": "New With Tags/Box", "like new": "Pre-Owned - Excellent", "good": "Pre-Owned - Good",
                "fair": "Pre-Owned - Fair", "poor": "Poor (Major flaws)"},
    "depop": {"brand_new": "New With Tags/Box", "used_like_new": "Pre-Owned - Excellent", "used_excellent": "Pre-Owned - Excellent",
              "used_good": "Pre-Owned - Good", "used_fair": "Pre-Owned - Fair"},
    # Sellwild's scale: 5 Brand New, 4 Like New, 3 Good, 2 Gently Used, 1 Quite Used.
    "sellwild": {"5": "New With Tags/Box", "4": "Pre-Owned - Excellent", "3": "Pre-Owned - Good", "2": "Pre-Owned - Good",
                 "1": "Pre-Owned - Fair"},
    "vinted": {"new with tags": "New With Tags/Box", "new without tags": "New Without Tags/Box", "very good": "Pre-Owned - Excellent",
               "good": "Pre-Owned - Good", "satisfactory": "Pre-Owned - Fair"},
}


class ChromeImportError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


def _name(marketplace: str) -> str:
    return _NAMES[marketplace]


def _check(marketplace: str) -> None:
    if marketplace not in MARKETPLACES:
        raise ChromeImportError(404, "Import through Chrome supports Poshmark, Mercari, Depop, Sellwild and Vinted.")


def listing_id_from(marketplace: str, value: str) -> str:
    """The listing ID from a pasted link or a bare ID."""
    _check(marketplace)
    text = str(value or "").strip()
    reference = listing_reference(marketplace, text)
    candidate = reference[0] if reference else (text.lower() if marketplace == "poshmark" else text)
    if not _LISTING_ID[marketplace].fullmatch(candidate):
        raise ChromeImportError(422, f"Paste a {_name(marketplace)} listing link or ID.")
    return candidate


async def _ask(marketplace: str, message_type: str, payload: dict) -> dict:
    try:
        reply = await browser_bridge.request(SimpleNamespace(id=None), message_type, {"marketplace": marketplace, **payload}, timeout=120)
    except browser_bridge.BrowserBridgeError as exc:
        raise ChromeImportError(503, f"{exc} Then sign in to {_name(marketplace)} in Chrome.") from exc
    if not isinstance(reply, dict):
        raise ChromeImportError(502, f"Chrome did not return {_name(marketplace)}'s listings.")
    if reply.get("ok") is not True:
        code = reply.get("error_code")
        status = 409 if code in {"busy", "rejected"} else 403 if code == "signed_out" else 429 if code == "rate_limited" else 502
        raise ChromeImportError(status, str(reply.get("error") or f"Chrome could not read {_name(marketplace)}."))
    return reply


def _price(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = round(float(value), 2)
    return number if math.isfinite(number) and 0 < number < 1_000_000 else None


def _quantity(value) -> int | None:
    return value if type(value) is int and 0 <= value <= 100_000 else None


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _similarity(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    overlap = 2 * len(left & right) / (len(left) + len(right))
    return max(SequenceMatcher(None, " ".join(sorted(left)), " ".join(sorted(right))).ratio(), overlap)


def _candidates(db: Session) -> list[tuple[str, str, set[str]]]:
    rows = db.query(Conversation.id, Conversation.title).filter(Conversation.title.isnot(None)).all()
    return [(conv_id, title, _tokens(title)) for conv_id, title in rows if title and title.strip()]


def _local_price(db: Session, conv_id: str) -> float | None:
    from vendoo_studio.models.listing import ListingRevision
    from vendoo_studio.repositories.queries import ListingRepo

    current = ListingRepo(db).get_current(conv_id)
    revision = db.get(ListingRevision, current.current_revision_id) if current and current.current_revision_id else None
    return _price((revision.listing_json or {}).get("price")) if revision else None


def suggested_match(db: Session, marketplace: str, title: str, price: float | None,
                    items: list[tuple[str, str, set[str]]]) -> dict | None:
    """The local item that looks most like this listing and has no listing on that marketplace yet.

    Only a suggestion: the seller decides whether to link it or import a new item.
    """
    from vendoo_studio.services.marketplace_delist import listings

    words = _tokens(title)
    # Only items that share a word are compared closely.
    scored = sorted(((_similarity(title, local), conv_id, local) for conv_id, local, tokens in items if tokens & words), reverse=True)
    for score, conv_id, local in scored[:5]:
        if score < MATCH_THRESHOLD:
            break
        if marketplace in listings(db, conv_id):
            continue
        local_price = _local_price(db, conv_id)
        return {"conversation_id": conv_id, "title": local, "score": round(score, 2), "price": local_price,
                "price_differs": bool(price and local_price and abs(local_price - price) > max(1.0, price * 0.1))}
    return None


async def active_listings(db: Session, marketplace: str, cursor: str | None) -> dict:
    _check(marketplace)
    reply = await _ask(marketplace, "marketplace.import.page", {"cursor": cursor})
    raw = reply.get("rows")
    if not isinstance(raw, list):
        raise ChromeImportError(502, f"Chrome did not return {_name(marketplace)}'s listings.")
    items = _candidates(db)
    rows = []
    for entry in raw[:MAX_ROWS]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not _LISTING_ID[marketplace].fullmatch(entry["id"]):
            continue
        listing_id = entry["id"]
        title = _text(entry.get("title"), 255) or listing_id
        price = _price(entry.get("price"))
        reason, existing, match = None, None, None
        try:
            existing = inventory_import.owner(db, marketplace, listing_id)
        except inventory_import.ImportConflict as exc:
            reason = str(exc)
        if price is None and not reason:
            reason = "This import supports listings priced in USD."
        if not existing and not reason:
            match = suggested_match(db, marketplace, title, price, items)
        rows.append({"id": listing_id, "title": title, "url": public_listing_url(marketplace, listing_id),
                     "price": f"{price:.2f}" if price is not None else None, "quantity": _quantity(entry.get("quantity")),
                     "reason": reason, "conversation_id": existing.id if existing else None, "match": match})
    next_cursor = reply.get("next_cursor")
    next_cursor = next_cursor if isinstance(next_cursor, str) and 0 < len(next_cursor) <= 300 else None
    return {"rows": rows, "total": None, "has_more": next_cursor is not None, "next_cursor": next_cursor}


def _condition(marketplace: str, value) -> str | None:
    text = _text(value, 60)
    if not text:
        return None
    mapped = _CONDITIONS[marketplace].get(text.lower())
    if mapped:
        return mapped
    try:
        guessed = ListingSchema.validate_condition(text)
    except ValueError:
        return None
    return guessed if guessed in VALID_CONDITIONS else None


def snapshot(marketplace: str, listing: dict) -> tuple[dict, list[str]]:
    """The local listing for a marketplace listing Chrome has already checked belongs to the seller."""
    title = _text(listing.get("title"), 255)
    price = _price(listing.get("price"))
    if len(title) > 80:
        # Anteroom's titles are at most 80 characters; the full title stays in the description.
        title = title[:80].rsplit(" ", 1)[0].rstrip(" ,-") or title[:80]
    if not title:
        raise ChromeImportError(409, f"{_name(marketplace)} did not return a title for this listing.")
    if price is None or listing.get("currency") != "USD":
        raise ChromeImportError(409, "This import supports listings priced in USD.")
    quantity = _quantity(listing.get("quantity"))
    category = _text(listing.get("category_path"), 300)
    category_id = _text(listing.get("category_id"), 120)
    condition = _condition(marketplace, listing.get("condition"))
    out: dict = {"title": title, "description": _text(listing.get("description"), 20000), "price": price,
                 "quantity": quantity if quantity and quantity > 0 else 1}
    for key, limit in (("brand", 120), ("size", 80), ("sku", 80)):
        value = _text(listing.get(key), limit)
        if value:
            out[key] = value
    if condition:
        out["condition"] = condition
    colors = [_text(color, 40) for color in listing.get("colors") or [] if _text(color, 40)]
    if colors:
        out["primaryColor"] = colors[0]
    if category:
        out["category_path"] = category
        out["marketplace_categories"] = {marketplace: category}
    if category_id:
        out["marketplace_category_ids"] = {marketplace: category_id}
    if marketplace == "sellwild":
        specifics = {key: int(value) for key, value in (("nativeCategoryId", category_id), ("nativeConditionId", listing.get("condition")))
                     if str(value or "").isdigit()}
        if specifics:
            out["sellwild_specifics"] = specifics
        if not category and specifics.get("nativeCategoryId"):
            from vendoo_studio.services.sellwild_listing import sellwild_catalog

            label = next((option["label"] for option in sellwild_catalog()["categories"] if option["id"] == specifics["nativeCategoryId"]), "")
            if label:
                out["category_path"] = label
                out["marketplace_categories"] = {"sellwild": label}
    photos = [url for url in listing.get("photos") or [] if isinstance(url, str) and url.startswith("https://")]
    return out, photos


async def _read(marketplace: str, listing_id: str) -> dict:
    reply = await _ask(marketplace, "marketplace.import.listing", {"listing_id": listing_id})
    listing = reply.get("listing")
    if not isinstance(listing, dict) or listing.get("id") != listing_id:
        raise ChromeImportError(409, f"{_name(marketplace)} returned a different listing. Nothing was imported.")
    return listing


async def import_listing(db: Session, marketplace: str, listing: str) -> dict:
    """Create a local item for one listing, or reopen the item that already has it."""
    listing_id = listing_id_from(marketplace, listing)
    try:
        existing = inventory_import.owner(db, marketplace, listing_id)
    except inventory_import.ImportConflict as exc:
        raise ChromeImportError(409, str(exc)) from exc
    if existing:
        return {"conversation_id": existing.id, "listing_id": listing_id, "reused": True, "linked": False,
                "photo_count": len(ConversationRepo(db).get_photos(existing.id)),
                "warnings": ["Already in Anteroom. Existing edits and photos were kept."]}
    data, photos = snapshot(marketplace, await _read(marketplace, listing_id))
    try:
        result = await inventory_import.save_owned_listing(db, marketplace, listing_id, data, photos, lambda: None)
    except inventory_import.ImportConflict as exc:
        raise ChromeImportError(409, str(exc)) from exc
    return {**result, "linked": False}


async def link_listing(db: Session, marketplace: str, listing: str, conversation_id: str) -> dict:
    """Link a listing to an item the seller chose. The item's own edits and photos stay as they are."""
    from vendoo_studio.services.marketplace_delist import DelistError, listings, mark

    listing_id = listing_id_from(marketplace, listing)
    if not ConversationRepo(db).get(conversation_id):
        raise ChromeImportError(404, "That Anteroom item no longer exists.")
    try:
        existing = inventory_import.owner(db, marketplace, listing_id)
    except inventory_import.ImportConflict as exc:
        raise ChromeImportError(409, str(exc)) from exc
    if existing and existing.id != conversation_id:
        raise ChromeImportError(409, "This listing is already linked to another Anteroom item.")
    if not existing:
        current = listings(db, conversation_id).get(marketplace) or {}
        if current.get("listing_id") and current["listing_id"] != listing_id:
            raise ChromeImportError(409, f"This item is already linked to another {_name(marketplace)} listing.")
        # The listing must still be the seller's and still for sale before it is linked.
        await _read(marketplace, listing_id)
        try:
            mark(db, conversation_id, marketplace, "listed", public_listing_url(marketplace, listing_id))
        except DelistError as exc:
            raise ChromeImportError(exc.status_code, str(exc)) from exc
    return {"conversation_id": conversation_id, "listing_id": listing_id, "reused": True, "linked": True,
            "photo_count": len(ConversationRepo(db).get_photos(conversation_id)),
            "warnings": [] if existing else ["Linked to the existing item. Its edits and photos were kept."]}
