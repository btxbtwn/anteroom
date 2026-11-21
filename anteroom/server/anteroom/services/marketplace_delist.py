"""Seller-confirmed delisting: delete an item's live listings on named marketplaces.

Delist means the listing is removed from the marketplace. eBay has no delete,
so its listing is ended. The
marketplace where the item sold is never touched. Nothing here runs on a
timer, a queue, or a retry: each call names its marketplaces and carries the
seller's confirmation, except the follow-up after a sale the seller turned
on (``sale_delist_followup``), which repeats a delist that did not confirm.
Deleting is safe to repeat, because every marketplace step reads the listing
first and reports one that is already gone as deleted. Already gone records
the listing as absent; it is never read as a sale.

The seller can also correct what Anteroom knows: mark a marketplace listed
(with its link), sold, or not listed. A mark changes only Anteroom's record,
never the marketplace, and stands until the marketplace reports something
newer: a sale after a "listed" mark, or a different listing after a "not
listed" one.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

from anteroom.models.conversation import Conversation
from anteroom.models.marketplace_listing import UNSETTLED_STATES
from anteroom.models.marketplace_sale import MarketplaceSale, PAID_STATUSES
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from anteroom.services import browser_bridge, marketplace_accounts
from anteroom.services.api_job_lock import claim_api_job, job_is_live, release_api_job, start_gate
from anteroom.services.listing_result import scrub
from anteroom.services.marketplace_identity import listing_id, listing_reference
from anteroom.services.vendoo_import import merge_notes, parse_notes

MARKETPLACES = ("ebay", "etsy", "poshmark", "mercari", "depop")
BROWSER_MARKETPLACES = {"poshmark", "mercari", "depop"}


class DelistError(RuntimeError):
    def __init__(self, status_code: int, detail: str, reason: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        # Sent as X-Anteroom-Reason so the app can offer the right next step.
        self.reason = reason


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _sold_on(db: Session, conv_id: str, notes: dict, native: dict) -> dict[str, datetime | None]:
    """Where the item sold, and when that was last reported (None when unknown)."""
    sold = reported_sales(notes)

    def add(marketplace: str, when) -> None:
        stamp, known = _time(when), sold.get(marketplace)
        sold[marketplace] = max(filter(None, (stamp, known)), default=None)

    from anteroom.services.sale_inventory import stock_after_sale

    stocks = {}
    for row in db.query(MarketplaceSale).filter(MarketplaceSale.conversation_id == conv_id, MarketplaceSale.order_status.in_(PAID_STATUSES)):
        mark = _notes_dict(notes, "marketplaceMarks").get(row.marketplace) or {}
        deleted = _notes_dict(notes, "marketplaceDelists").get(row.marketplace)
        url, _source = current_listing(row.marketplace, (native.get(row.marketplace) or {}).get("listing_url"),
                                       _notes_dict(notes, "vendooListingUrls").get(row.marketplace), mark,
                                       deleted if isinstance(deleted, dict) else {})
        current_id = listing_id(row.marketplace, url)
        if current_id and row.listing_id and current_id != row.listing_id:
            continue
        key = (row.marketplace, row.listing_id)
        if key not in stocks:
            stocks[key] = stock_after_sale(db, conv_id, row.marketplace, row.listing_id)
        if stocks[key]["status"] == "sold_out":
            add(row.marketplace, row.received_at)
    return sold


def reported_sales(notes: dict) -> dict[str, datetime | None]:
    """Sale reports stored on the item, without querying native sale records."""
    sold: dict[str, datetime | None] = {}
    sale = notes.get("vendooSale")
    if not isinstance(sale, dict):
        sale = {}
    if sale.get("marketplace"):
        sold[str(sale["marketplace"]).lower()] = None
    dates = notes.get("vendooDates")
    if not isinstance(dates, dict):
        dates = {}
    sold_by = dates.get("soldByMarketplace")
    if not isinstance(sold_by, dict):
        sold_by = {}
    for name, when in sold_by.items():
        if when:
            marketplace = str(name).lower()
            sold[marketplace] = max(filter(None, (_time(when), sold.get(marketplace))), default=None)
    return sold


def _notes_dict(notes: dict, key: str) -> dict:
    value = notes.get(key)
    return value if isinstance(value, dict) else {}


def current_listing(marketplace: str, anteroom_url, vendoo_url, mark: dict, deleted: dict) -> tuple[str | None, str | None]:
    """The listing link that names this marketplace's listing now, and who reported it."""
    candidates = [(anteroom_url, "anteroom"), (vendoo_url, "vendoo")]
    if mark.get("state") == "listed":
        marked_id = listing_id(marketplace, mark.get("url"))
        direct_id = listing_id(marketplace, anteroom_url)
        # A mark for the removed listing cannot mask a receipt for a different
        # live listing. Keep ordinary seller corrections and same-ID delists.
        superseded = (deleted.get("state") == "deleted" and marked_id
                      and deleted.get("listing_id") == marked_id and direct_id and direct_id != marked_id)
        if not superseded:
            candidates.insert(0, (mark.get("url"), "seller"))
    return next(((url, source) for url, source in candidates if listing_id(marketplace, url)), (None, None))


def removed_marketplaces(notes: dict, anteroom_urls: dict[str, str]) -> list[str]:
    """Marketplaces where Anteroom deleted the listing, or the seller marked it not listed.

    Vendoo is never told about a direct delist, so its record keeps calling the
    item listed there. A newer listing than the one removed counts as live again.
    """
    urls = _notes_dict(notes, "vendooListingUrls")
    delists = _notes_dict(notes, "marketplaceDelists")
    marks = _notes_dict(notes, "marketplaceMarks")
    removed = []
    for marketplace in MARKETPLACES:
        mark = marks.get(marketplace)
        if not isinstance(mark, dict):
            mark = {}
        record = delists.get(marketplace)
        if not isinstance(record, dict):
            record = {}
        # Inventory reads call this for every item. Only removal records need
        # URL resolution to check whether a newer listing superseded them.
        if mark.get("state") != "not_listed" and record.get("state") != "deleted":
            continue
        url, _source = current_listing(marketplace, anteroom_urls.get(marketplace), urls.get(marketplace), mark, record)
        item_id = listing_id(marketplace, url)
        if mark.get("state") == "not_listed" and (not item_id or item_id == mark.get("listing_id")):
            removed.append(marketplace)
        elif record.get("state") == "deleted" and (not item_id or item_id == record.get("listing_id")):
            removed.append(marketplace)
    return removed


def listings(db: Session, conv_id: str, *, conv: Conversation | None = None, native: dict | None = None) -> dict:
    """Each marketplace listing Anteroom knows for this item, and what Delist would do."""
    if conv is None:
        conv = ConversationRepo(db).get(conv_id)
    if not conv:
        raise DelistError(404, "Conversation not found")
    notes = parse_notes(conv.notes)
    if native is None:
        native = MarketplaceListingRepo(db).for_conversation(conv_id)
    return known_listings(notes, native, _sold_on(db, conv_id, notes, native))


def listings_for(db: Session, convs: list[Conversation]) -> dict[str, dict]:
    """``listings`` for many loaded items, in a fixed number of queries.

    Only items with a paid sale read their sale records one by one, because
    whether that sale ended the listing depends on stock read per listing.
    """
    from anteroom.models.marketplace_listing import MarketplaceListing
    from anteroom.repositories.marketplace_listings import as_dict

    ids = [conv.id for conv in convs]
    native: dict[str, dict[str, dict]] = {}
    for row in db.query(MarketplaceListing).filter(MarketplaceListing.conversation_id.in_(ids)).populate_existing():
        native.setdefault(row.conversation_id, {})[row.marketplace] = as_dict(row)
    with_sales = {conv_id for (conv_id,) in db.query(MarketplaceSale.conversation_id).filter(
        MarketplaceSale.conversation_id.in_(ids), MarketplaceSale.order_status.in_(PAID_STATUSES)).distinct()}
    result = {}
    for conv in convs:
        notes = parse_notes(conv.notes)
        bindings = native.get(conv.id, {})
        sold = _sold_on(db, conv.id, notes, bindings) if conv.id in with_sales else reported_sales(notes)
        result[conv.id] = known_listings(notes, bindings, sold)
    return result


def known_listings(notes: dict, native: dict, sold: dict) -> dict:
    """Interpret already-loaded listing links, removals and sale reports."""
    urls = _notes_dict(notes, "vendooListingUrls")
    delists = _notes_dict(notes, "marketplaceDelists")
    marks = _notes_dict(notes, "marketplaceMarks")
    result = {}
    for marketplace in MARKETPLACES:
        binding = native.get(marketplace)
        if not isinstance(binding, dict):
            binding = {}
        mark = marks.get(marketplace)
        if not isinstance(mark, dict):
            mark = {}
        record = delists.get(marketplace)
        if not isinstance(record, dict):
            record = {}
        url, source = current_listing(marketplace, binding.get("listing_url"), urls.get(marketplace), mark, record)
        item_id = listing_id(marketplace, url)
        if mark.get("state") == "not_listed" and (not item_id or item_id == mark.get("listing_id")):
            continue
        sold_here = marketplace in sold
        if mark.get("state") == "listed":
            reported, marked = sold.get(marketplace), _time(mark.get("at"))
            sold_here = bool(reported and marked and reported > marked)
        elif mark.get("state") == "sold":
            sold_here = True
        if not url and not record and not sold_here:
            continue
        item_id = item_id or record.get("listing_id")
        # A newer listing than the one deleted is live again.
        if record and item_id and record.get("listing_id") != item_id:
            record = {}
        result[marketplace] = {"url": url or record.get("url"), "listing_id": item_id, "source": source,
                               "sold_here": sold_here, "state": record.get("state") or "live",
                               "error": record.get("error"), "marketplace_message": record.get("marketplace_message"),
                               "at": record.get("at"), "attempts": record.get("attempts"),
                               "already_gone": record.get("already") is True}
    return result


def _locked_conversation(db: Session, conv_id: str) -> Conversation:
    """The item, read after this transaction holds its row until commit.

    Notes are read, merged and written back whole, so a read before the lock
    could overwrite another writer's change, such as a sale undo removing its
    Sold mark. A no-op write takes the lock on both databases: Postgres locks
    the row, SQLite takes its single writer lock (its reads never lock). Hold
    it only for the local read-merge-commit, never across a marketplace call.
    """
    db.execute(update(Conversation).where(Conversation.id == conv_id)
               .values(notes=Conversation.notes, updated_at=Conversation.updated_at)
               .execution_options(synchronize_session=False))
    conv = db.query(Conversation).filter(Conversation.id == conv_id).populate_existing().first()
    assert conv is not None
    return conv


def _store(db: Session, conv_id: str, key: str, marketplace: str, value: dict | None) -> None:
    conv = _locked_conversation(db, conv_id)
    current = _notes_dict(parse_notes(conv.notes), key)
    current = {name: entry for name, entry in current.items() if name != marketplace}
    conv.notes = merge_notes(conv.notes, {key: {**current, **({marketplace: value} if value else {})}})
    db.commit()


def _record(db: Session, conv_id: str, marketplace: str, value: dict) -> None:
    _store(db, conv_id, "marketplaceDelists", marketplace, value)


SALE_PINS = "saleDelistTargets"


def sale_pins(notes: dict) -> dict[str, dict[str, str]]:
    """The listing each sale left up on each other marketplace, by sale ID."""
    pins = {}
    for sale_id, pin in _notes_dict(notes, SALE_PINS).items():
        targets = pin.get("targets") if isinstance(pin, dict) else None
        if isinstance(targets, dict):
            pins[sale_id] = {name: str(item) for name, item in targets.items() if isinstance(item, str) and item}
    return pins


def pin_sale(db: Session, conv_id: str, sale_id: str, targets: dict[str, str]) -> dict[str, str]:
    """Record once which listings a sale left up elsewhere, and return what is recorded.

    The first record stands: a listing put up later, such as a relist, is never added to it.
    """
    conv = _locked_conversation(db, conv_id)
    pinned = sale_pins(parse_notes(conv.notes)).get(sale_id)
    if pinned is not None:
        db.rollback()
        return pinned
    pins = _notes_dict(parse_notes(conv.notes), SALE_PINS)
    conv.notes = merge_notes(conv.notes, {SALE_PINS: {**pins, sale_id: {"targets": targets, "at": _now()}}})
    db.commit()
    return targets


def _forget_deleted_listing(db: Session, conv_id: str, marketplace: str, deleted_id: str | None) -> None:
    """A confirmed delete of Anteroom's listing leaves nothing live, so its record rests as Mark as not listed leaves it.

    Without this the record still reads published and List refuses the item
    as already listed. The marketplace IDs stay, so a new List reuses them.
    A delete of a different listing (a duplicate the seller linked) leaves
    Anteroom's own listing live, so its record stays.
    """
    records = MarketplaceListingRepo(db)
    record = records.get(conv_id, marketplace)
    if record.get("state") not in {"published", "pending_review"}:
        return
    live_id = listing_id(marketplace, record.get("listing_url"))
    if live_id and deleted_id and str(live_id) != str(deleted_id):
        return
    from anteroom.services.native_marketplaces import _resting_state

    records.save(conv_id, marketplace, {"error": None, "error_code": None, "error_fields": None, "marketplace_message": None,
                                        "retry_after": None, "etsy_pending_write": None,
                                        "state": _resting_state(marketplace, record), "listing_url": None,
                                        "listing_id": None, "revision_id": None, "listed_price": None})


MARKS = ("listed", "sold", "not_listed")


def mark(db: Session, conv_id: str, marketplace: str, state: str, url: str | None = None,
         relink_deleted: bool = False) -> dict:
    # Serialise duplicate validation and the local write with other seller
    # marks and direct action starts in this process.
    with start_gate():
        return _mark(db, conv_id, marketplace, state, url, relink_deleted)


def _mark(db: Session, conv_id: str, marketplace: str, state: str, url: str | None, relink_deleted: bool = False) -> dict:
    """Record what the seller says about one marketplace. The marketplace itself is not touched.

    A mark also settles an unconfirmed direct listing: the seller has looked.
    """
    if marketplace not in MARKETPLACES or state not in MARKS:
        raise DelistError(400, "Name a marketplace and what to mark it as.")
    if any(str(job.current_step or "") == "native_delist" and job.conversation_id == conv_id for job in JobRepo(db).get_running()):
        raise DelistError(409, "A delist is running. Wait for it to finish.")
    known = listings(db, conv_id).get(marketplace) or {}
    # Full precision: a sale reported later in the same second must still count as newer.
    value: dict = {"state": state, "at": datetime.now(UTC).isoformat()}
    if state == "listed":
        reference = listing_reference(marketplace, url)
        if not reference:
            raise DelistError(400, f"Paste the {marketplace.title()} listing link, as it appears in the browser's address bar.")
        item_id, clean = reference
        from anteroom.services.relay_sales import matching_listings

        if any(conv.id != conv_id for conv in matching_listings(db, marketplace, item_id)):
            raise DelistError(409, "This marketplace listing is already linked to another item. Resolve the duplicate before linking it here.")
        conv = ConversationRepo(db).get(conv_id)
        if conv is not None:
            # A delist writes through its own sessions; read the record it left.
            db.refresh(conv)
        deleted = _notes_dict(parse_notes(conv.notes if conv else None), "marketplaceDelists").get(marketplace)
        if not relink_deleted and isinstance(deleted, dict) and deleted.get("state") == "deleted" and deleted.get("listing_id") == item_id:
            # An old link pasted back by mistake would make Update, offers and
            # Delist act on a listing that is gone. Only the seller's look can say otherwise.
            name = marketplace.title()
            raise DelistError(409, f"Anteroom delisted this {name} listing. Paste the link to the listing that is live on {name} now, "
                                   f"or confirm it is still there if you just checked.", reason="deleted_listing")
        value.update(url=clean, listing_id=item_id)
    else:
        value.update(url=known.get("url"), listing_id=known.get("listing_id"))
    records = MarketplaceListingRepo(db)
    record = records.get(conv_id, marketplace)
    if record.get("job_id") and job_is_live(db, record["job_id"]):
        raise DelistError(409, f"The {marketplace.title()} action is still running. Wait for it to finish.")
    cleared = {"error": None, "error_code": None, "error_fields": None, "marketplace_message": None, "retry_after": None,
               "etsy_pending_write": None}
    if state == "not_listed" and record.get("state") in {*UNSETTLED_STATES, "published", "pending_review"}:
        from anteroom.services.native_marketplaces import _resting_state

        # Not live: keep the marketplace IDs so a new List reuses them, drop the link.
        records.save(conv_id, marketplace, {**cleared, "state": _resting_state(marketplace, record), "listing_url": None,
                                            "listing_id": None, "revision_id": None, "listed_price": None})
    elif state == "listed" and record.get("state") != "published":
        # The seller looked and the listing is there. That is true whether the
        # record was unconfirmed or had already been reset to a resting state
        # after a publish Anteroom could not confirm: the mark is the evidence
        # the machine record was missing, so it becomes live here.
        records.save(conv_id, marketplace, {**cleared, "state": "published", "listing_url": value["url"],
                                            "listing_id": value["listing_id"], "listed_price": None})
    elif state != "not_listed" and record.get("state") in UNSETTLED_STATES:
        # The seller saw it listed, not at which price: the unconfirmed action may not have changed it.
        records.save(conv_id, marketplace, {**cleared, "state": "published", "listed_price": None})
    if record.get("job_id") and record.get("state") in UNSETTLED_STATES and JobRepo(db).get(record["job_id"]):
        JobRepo(db).add_event(record["job_id"], "native_marked", None, {"marketplace": marketplace, "mark": state})
    _store(db, conv_id, "marketplaceMarks", marketplace, value)
    # Marking listed or not listed starts the record over; a sale keeps how the listing ended.
    if state != "sold":
        _store(db, conv_id, "marketplaceDelists", marketplace, None)
    if state == "listed":
        from anteroom.services.relay_sales import reconcile_unmatched

        reconcile_unmatched(db, conversation_id=conv_id)
    return listings(db, conv_id)


async def _delete_ebay(item_id: str, record: dict | None = None) -> dict:
    from anteroom.services.ebay_client import EbayClient, EbayFailure

    client = await EbayClient.connect()
    record = record or {}
    group_key = str(record.get("ebay_group_key") or "")
    offers = record.get("ebay_variant_offers")
    if not isinstance(offers, dict):
        offers = {}
    if group_key and offers and str(record.get("ebay_listing_id") or "") == item_id:
        # A listing with variations Anteroom made: end the whole group, and keep its offers to list again.
        from anteroom.services.ebay_variations import end_group

        return await end_group(client, group_key, [str(offer_id) for offer_id in offers.values()])
    try:
        await client.trading("EndFixedPriceItem", {"ItemID": item_id, "EndingReason": "NotAvailable"})
    except EbayFailure as exc:
        # 1047: the listing has already ended. Anything else is the seller's to check.
        if "1047" in str(exc):
            return {"ok": True, "state": "deleted", "already": True}
        raise
    return {"ok": True, "state": "deleted"}


async def _delete_etsy(item_id: str) -> dict:
    from anteroom.services.etsy_listings import connected_shop_id

    shop_id = connected_shop_id()
    try:
        remote = await marketplace_accounts.etsy_api("GET", f"listings/{item_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {"ok": True, "state": "deleted", "already": True}
        raise
    if str(remote.get("shop_id")) != shop_id:
        raise ValueError("This Etsy listing belongs to another shop. Connect the shop that owns it.")
    if remote.get("state") == "sold_out":
        raise ValueError("This Etsy listing has sold out. It was not deleted.")
    await marketplace_accounts.etsy_api("DELETE", f"listings/{item_id}")
    return {"ok": True, "state": "deleted"}


def _marketplace_message(marketplace: str, exc: Exception) -> str | None:
    if marketplace == "etsy":
        from anteroom.services.etsy_listings import marketplace_message

        return marketplace_message(exc)
    return scrub(getattr(exc, "marketplace_message", None))


def _failure_text(marketplace: str, exc: Exception) -> str:
    from anteroom.services.etsy_listings import _error_text

    if marketplace == "etsy":
        return _error_text(exc)
    if isinstance(exc, browser_bridge.BrowserBridgeError):
        return "Chrome did not confirm the delete. Delist again to check; it is safe to repeat."
    return str(exc) or "The marketplace did not confirm the delete. Delist again to check; it is safe to repeat."


async def delist(conv_id: str, marketplaces: list[str], confirm: bool, *, expected: dict[str, str] | None = None,
                 sale_id: str | None = None) -> dict:
    """Delete the named listings. Runs as a job holding the automation slot, so no Send starts into Chrome meanwhile.

    ``expected`` pins the listing ID to delete on each marketplace: when one names
    another listing now, such as a relist, nothing is deleted. ``sale_id`` names the
    sale a delist after a sale follows: once that order is no longer paid, or the
    item has stock again, nothing is deleted. A follow-up stops before its
    next marketplace if automatic delisting is turned off.

    Each database step runs on a worker thread with its own session; none is open while
    eBay, Etsy or Chrome answers.
    """
    from types import SimpleNamespace

    from anteroom.database import in_worker_session
    from anteroom.services.sale_delist_followup import _enabled

    names, known, job_id = await in_worker_session(_open_delist, conv_id, marketplaces, confirm, expected, sale_id)
    job = SimpleNamespace(id=job_id)
    failed = False
    skipped: list[str] = []
    try:
        for index, name in enumerate(names):
            if sale_id is not None and not await asyncio.to_thread(_enabled):
                skipped = names[index:]
                break
            entry = known[name]
            base = {"listing_id": entry["listing_id"], "url": entry["url"], "at": _now(),
                    "attempts": _attempt(entry)}
            try:
                if sale_id is None:
                    ebay_record = await in_worker_session(_start_delete, conv_id, name, base, job_id)
                else:
                    ebay_record = await in_worker_session(_start_followup_delete, conv_id, name, base, job_id, sale_id)
            except DelistError as exc:
                if exc.reason != "sale_changed":
                    raise
                skipped = names[index:]
                break
            try:
                if name == "ebay":
                    result = await _delete_ebay(entry["listing_id"], ebay_record)
                elif name == "etsy":
                    result = await _delete_etsy(entry["listing_id"])
                else:
                    result = await browser_bridge.request(job, "marketplace.delete", {
                        "marketplace": name, "listing_id": entry["listing_id"], "confirm": True,
                    }, timeout=90)
                gone = _mercari_gone(name, result)
                if result.get("ok") is True and result.get("state") == "deleted" or gone:
                    # Already absent is a reconciliation of the listing, never evidence of a sale.
                    # Who removed it is not known: only that the marketplace no longer has it.
                    already = gone or result.get("already") is True
                    await in_worker_session(_deleted, conv_id, name, {
                        **base, "already": True, "marketplace_message": scrub(result.get("marketplace_message")),
                    } if already else base)
                else:
                    failed = True
                    await in_worker_session(_record, conv_id, name, {
                        **base, "state": "failed", "at": _now(),
                        "error": str(result.get("error") or "The marketplace did not confirm the delete.")[:300],
                        "marketplace_message": scrub(result.get("marketplace_message"))})
            except Exception as exc:  # noqa: BLE001 - recorded per marketplace for the seller
                failed = True
                await in_worker_session(_record, conv_id, name, {
                    **base, "state": "failed", "at": _now(), "error": _failure_text(name, exc)[:300],
                    "marketplace_message": _marketplace_message(name, exc)})
        await in_worker_session(lambda db: JobRepo(db).update_status(
            job_id, "failed" if failed else "completed", "native_delist",
            error="A delist did not finish." if failed else None,
            # A follow-up retry alerts the operator itself, on its first try and every few after.
            failure_log_level=logging.INFO if sale_id else logging.ERROR))
    except BaseException:
        await asyncio.shield(in_worker_session(lambda db: JobRepo(db).update_status(
            job_id, "failed", "native_delist", error="The delist stopped before it finished.")))
        raise
    finally:
        release_api_job(job_id)
    result = await in_worker_session(listings, conv_id)
    # Skipped marketplaces may have failures from a prior sweep; don't report them as new tries.
    for name in skipped:
        result.pop(name, None)
    return result


def _attempt(entry: dict) -> int:
    """Which try this is at deleting this listing: a failed one before it counts."""
    previous = entry.get("attempts") if entry.get("state") in {"failed", "deleting"} else 0
    return (previous if isinstance(previous, int) and previous > 0 else 0) + 1


def _mercari_gone(name: str, result: dict) -> bool:
    """Mercari refuses the read of a deleted item with these words (2026-11-20).

    Transition for extensions without the check in ``deleteMercari``: remove
    on 2027-01-31, once installed extensions report it as already deleted.
    """
    message = str(result.get("marketplace_message") or "").strip().rstrip(".").lower()
    return name == "mercari" and result.get("error_code") == "rejected" and message == "item is no longer available"


def _open_delist(db: Session, conv_id: str, marketplaces: list[str], confirm: bool,
                 expected: dict[str, str] | None = None, sale_id: str | None = None) -> tuple[list[str], dict, str]:
    from anteroom.services.extension_connection import current_extension

    from anteroom.services import listing_mode

    listing_mode.require("direct", db)
    names = list(dict.fromkeys(str(m).strip().lower() for m in marketplaces if str(m or "").strip()))
    if confirm is not True:
        raise DelistError(400, "Confirm the delist before anything is deleted.")
    if not names or any(name not in MARKETPLACES for name in names):
        raise DelistError(400, "Name the marketplaces to delist.")
    with listing_mode.guard(db, "direct"):
        known = listings(db, conv_id)
        for name in names:
            entry = known.get(name)
            if not entry or not entry["listing_id"]:
                raise DelistError(409, f"Anteroom has no {name} listing link for this item.")
            if entry["sold_here"]:
                raise DelistError(409, f"This item sold on {name}. That listing is never deleted.")
        changed = [name for name in names if expected is not None and known[name]["listing_id"] != expected.get(name)]
        if changed:
            raise DelistError(409, f"The {', '.join(changed)} listing changed. Nothing was delisted.", reason="listing_changed")
        sold_at = _require_followup_sale(db, conv_id, sale_id, names) if sale_id is not None else None
        if BROWSER_MARKETPLACES & set(names) and not current_extension().connected:
            raise DelistError(409, "Connect Chrome first.")
        repo = JobRepo(db)
        listing = ListingRepo(db).get_current(conv_id)
        if sale_id is not None:
            from anteroom.services.sale_delist_followup import _went_up_after

            # A seller can mark a listing live again after the sweep selected it.
            conv = db.get(Conversation, conv_id, populate_existing=True)
            notes = parse_notes(conv.notes if conv else None)
            marks = _notes_dict(notes, "marketplaceMarks")
            native = MarketplaceListingRepo(db).for_conversation(conv_id)
            if any(_went_up_after(name, known[name]["listing_id"], native, marks, sold_at) for name in names):
                raise DelistError(409, "A marketplace listing was listed again after the sale. Nothing was delisted.",
                                  reason="listing_changed")
        if repo.get_active():
            raise DelistError(409, "Another automation job is queued or running. Wait for it to finish.")
        job = repo.create(conv_id, listing.current_revision_id if listing else "", {"delist": names},
                          status="dispatched", current_step="native_delist")
        claim_api_job(db, job.id)
    return names, known, job.id


def _require_followup_sale(db: Session, conv_id: str, sale_id: str, names: list[str], *,
                           lock: bool = False) -> datetime:
    from anteroom.services.sale_delist_followup import WINDOW
    from anteroom.services.sale_inventory import stock_after_sale

    sale = db.get(MarketplaceSale, sale_id, with_for_update=lock, populate_existing=True)
    sold_at = _time(sale.sold_at or sale.received_at) if sale else None
    if (sale is None or str(sale.conversation_id) != conv_id or sale.source != "marketplace"
            or sale.order_status not in PAID_STATUSES or sale.marketplace in names
            or sold_at is None or sold_at < datetime.now(UTC) - WINDOW
            or stock_after_sale(db, conv_id, sale.marketplace, sale.listing_id)["status"] != "sold_out"):
        raise DelistError(409, "The sale changed: it is no longer paid or the item has stock.", reason="sale_changed")
    return sold_at


def _start_followup_delete(db: Session, conv_id: str, name: str, base: dict, job_id: str,
                           sale_id: str) -> dict | None:
    # Serialize the local start with order recovery; the row lock also fences
    # concurrent writes to this sale from other processes until the start commits.
    # No database connection or lock is held while the marketplace responds.
    with start_gate():
        _require_followup_sale(db, conv_id, sale_id, [name], lock=True)
        return _start_delete(db, conv_id, name, base, job_id)


def _start_delete(db: Session, conv_id: str, name: str, base: dict, job_id: str) -> dict | None:
    """Record the delete as started. Returns the eBay listing record the eBay delete reads."""
    _record_delete(db, conv_id, name, {**base, "state": "deleting"}, commit=False)
    ebay_record = MarketplaceListingRepo(db).get(conv_id, "ebay") if name == "ebay" else None
    # add_event commits both the progress event and the deleting record.
    JobRepo(db).add_event(job_id, "native_progress", "native_delist", {"marketplace": name, "step": "delist", "label": "Delisting"})
    return ebay_record


def _record_delete(db: Session, conv_id: str, name: str, value: dict, *, commit: bool = True) -> None:
    """Write a delete record, in one notes write with retiring a superseded seller link.

    Delist targets the seller's "listed" link unless a newer receipt superseded
    it, so a delete of a different listing than the marked one means the mark
    names an older, already removed listing. The record of that removal is
    replaced here, so the mark goes with it: kept, it would read as live again
    while this delete runs, after it fails, or once its receipt is cleared.
    """
    conv = _locked_conversation(db, conv_id)
    notes = parse_notes(conv.notes)
    delists = {key: entry for key, entry in _notes_dict(notes, "marketplaceDelists").items() if key != name}
    updates: dict = {"marketplaceDelists": {**delists, name: value}}
    marks = _notes_dict(notes, "marketplaceMarks")
    mark = marks.get(name)
    if isinstance(mark, dict) and mark.get("state") == "listed" and listing_id(name, mark.get("url")) != value.get("listing_id"):
        updates["marketplaceMarks"] = {key: entry for key, entry in marks.items() if key != name}
    conv.notes = merge_notes(conv.notes, updates)
    if commit:
        db.commit()


def _deleted(db: Session, conv_id: str, name: str, base: dict) -> None:
    _record_delete(db, conv_id, name, {**base, "state": "deleted", "error": None, "at": _now()})
    _forget_deleted_listing(db, conv_id, name, base.get("listing_id"))
