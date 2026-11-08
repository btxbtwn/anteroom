"""An item's inventory status from Anteroom's own listings and sales.

Vendoo's synced label (``notes.vendooStatus``) is the imported history. On top
of it, Anteroom's own records decide:

- **Sold** once the units sold reach the item's quantity. A unit is a paid
  line on the sales ledger (a sale the marketplaces reported, or one the
  seller recorded with Mark as sold), or an older seller mark "sold" on a
  marketplace with no line of its own. A direct stock read showing the listing
  sold out also counts. Vendoo's own sale records and dates are not evidence:
  a sale is Anteroom's to see, on the marketplace itself.
- **Active** while the item is live somewhere: a direct listing, a seller
  "listed" mark, or a Vendoo listing Anteroom has not delisted. An item with
  some units sold and some left stays Active, with ``units_sold`` set.
- **Draft** otherwise, including a Vendoo "active" item Anteroom delisted
  everywhere.

Reads only. Nothing here lists, delists or tells a marketplace anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from vendoo_studio.models.listing import ListingRevision
from vendoo_studio.models.marketplace_listing import LIVE_STATES, MarketplaceListing
from vendoo_studio.models.marketplace_sale import PAID_STATUSES, MarketplaceSale

VENDOO_STATUSES = ("draft", "active", "sold")


@dataclass(frozen=True)
class ItemStatus:
    status: str
    units_sold: int = 0
    quantity: int = 1
    # When Anteroom's records last saw a unit sell; None when nothing sold here.
    sold_at: datetime | None = None
    # True when Anteroom's own sales, listings, marks or delists decided it,
    # rather than Vendoo's label alone.
    own: bool = False

    @property
    def partially_sold(self) -> bool:
        return self.status == "active" and 0 < self.units_sold < self.quantity


def _units(value) -> int:
    return value if type(value) is int and value > 0 else 1


def _time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _quantities(db: Session, conv_ids: set[str] | None, first_sales: dict[str, datetime] | None = None) -> dict[str, int]:
    """Use the last correction before sales, then retain the established stock.

    Before the first recorded sale, quantity edits correct the stock baseline.
    Afterward, a lower remaining quantity cannot erase stock already accounted
    for; an increase can raise the total. Marketplace stock observations remain
    separate and can establish that the remaining units have actually sold out.
    """
    first_sales = first_sales or {}
    best: dict[str, int] = {}

    def add(conv_id: str, value: int | None, created_at: datetime | None):
        if value is None:
            return
        try:
            number = int(value)
        except (TypeError, ValueError):
            return
        if number < 1:
            return
        first_sale = first_sales.get(conv_id)
        when = _time(created_at)
        if first_sale is None or (when is not None and when <= first_sale) or number > best.get(conv_id, 0):
            best[conv_id] = number

    # This scalar is recorded on each immutable revision. JSON extraction here
    # made each poll parse every historical marketplace form in the database.
    every = db.query(ListingRevision.conversation_id, ListingRevision.inventory_quantity,
                     ListingRevision.created_at).order_by(ListingRevision.created_at)
    if conv_ids is not None:
        every = every.filter(ListingRevision.conversation_id.in_(conv_ids))
    for conv_id, quantity, created_at in every.all():
        add(conv_id, quantity, created_at)
    return best



def sale_moment(line: MarketplaceSale) -> datetime | None:
    """When a ledger line sold, as Analytics dates it. None once the seller cleared the date."""
    raw = line.financials
    if isinstance(raw, dict) and "sold_on" in raw and raw["sold_on"] is None:
        return None
    return _time(line.sold_at or line.received_at)


def counted_sales(lines: list[MarketplaceSale], quantity: int) -> list[MarketplaceSale]:
    """A sold item's sales, oldest first, no more than the units it had in stock.

    When the seller sells on one marketplace and clears the item's other
    listings, each check that reads one of those listings as sold records a
    line with no order behind it. Keep every line that has an order, and only
    as many of the rest as the item had units; a genuine relist that sold
    again has its own order or listing report.
    """
    ordered = sorted(lines, key=lambda line: (line.sold_at or line.received_at, line.id))
    room = max(0, max(1, quantity) - sum(line.is_order_backed for line in ordered))
    reported = [line for line in ordered if not line.is_order_backed][:room]
    return [line for line in ordered if line.is_order_backed or line in reported]


def _read_sold_out(observations: dict[str, dict], lines: list[MarketplaceSale]) -> bool:
    """A direct stock read, taken after the latest sale on that listing, showing no units left."""
    for line in lines:
        observed = observations.get(line.marketplace) or {}
        checked = _time(observed.get("checked_at"))
        latest = max(filter(None, (_time(other.received_at) for other in lines
                                   if (other.marketplace, other.listing_id) == (line.marketplace, line.listing_id))), default=None)
        if observed.get("listing_id") != line.listing_id or not checked or not latest or checked < latest:
            continue
        if observed.get("state") == "sold_out" or (observed.get("state") == "active" and observed.get("quantity") == 0):
            return True
    return False


# How far apart Vendoo's date and the marketplace's date for one sale can be.
SAME_SALE_DAYS = 7


def _within_days(first: datetime | None, second: datetime | None, days: int) -> bool:
    return first is not None and second is not None and abs((first - second).total_seconds()) <= days * 86400


def repeated_lines(lines: list[MarketplaceSale]) -> set[str]:
    """IDs of one item's paid lines that record a sale another of its lines already records.

    - The Vendoo sale kept on the ledger, once that marketplace's own order for
      it is on the ledger too: the order replaces it, one copy per order, the
      nearest in date within a week. Webhooks and order reads can save the order without
      removing the copy, so it is left out wherever sales are counted.
    - An eBay line saved a second time under its bare transaction ID
      (``<transaction>``) beside ``<item>-<transaction>``.
    """
    from vendoo_studio.services.sale_matching import matching_sale_index

    repeated: set[str] = set()
    orders = [line for line in lines if line.source == "marketplace" and line.is_order_backed]
    for line in orders:
        if line.marketplace == "ebay" and any(
                other is not line and other.marketplace == "ebay" and other.order_id == line.order_id
                and other.line_id.endswith(f"-{line.line_id}") for other in orders):
            repeated.add(line.id)
    copies = [line for line in lines if line.source == "vendoo"]
    for order in sorted((line for line in orders if line.id not in repeated),
                        key=lambda line: (line.sold_at or line.received_at, line.id)):
        moment = sale_moment(order)
        match = matching_sale_index(copies, order.marketplace, moment)
        # A copy from an earlier sale of a relisted item is a sale of its own.
        if match is not None and _within_days(sale_moment(copies[match]), moment, SAME_SALE_DAYS):
            repeated.add(copies.pop(match).id)
    return repeated


def paid_lines(db: Session, conv_ids: set[str] | None = None) -> dict[str, list[MarketplaceSale]]:
    """Each item's paid sales, every sale once (see ``repeated_lines``)."""
    query = db.query(MarketplaceSale).filter(MarketplaceSale.conversation_id.isnot(None),
                                             MarketplaceSale.order_status.in_(PAID_STATUSES))
    if conv_ids is not None:
        query = query.filter(MarketplaceSale.conversation_id.in_(conv_ids))
    grouped: dict[str, list[MarketplaceSale]] = {}
    for line in query.all():
        if line.conversation_id is not None:
            grouped.setdefault(line.conversation_id, []).append(line)
    for conv_id, own in grouped.items():
        repeated = repeated_lines(own)
        if repeated:
            grouped[conv_id] = [line for line in own if line.id not in repeated]
    return grouped


def statuses(db: Session, conversations: list[tuple[str, dict]]) -> dict[str, ItemStatus]:
    """Status for each ``(conversation_id, parsed notes)``, with a few bulk reads."""
    if not conversations:
        return {}

    from vendoo_studio.services.marketplace_delist import removed_marketplaces

    ids = {conv_id for conv_id, _notes in conversations}
    if not ids:
        return {}
    scoped = ids if len(ids) <= 500 else None
    lines = paid_lines(db, scoped)
    first_sales = {}
    for conv_id, notes in conversations:
        own_lines = lines.get(conv_id, [])
        # Use when Anteroom first learned about a sale: imported/backdated orders
        # must not turn corrections made before their arrival into restocks.
        moments = [_time(line.received_at or line.sold_at) for line in own_lines]
        marks = notes.get("marketplaceMarks")
        if isinstance(marks, dict):
            moments.extend(_time(mark.get("at")) for market, mark in marks.items()
                           if isinstance(mark, dict) and mark.get("state") == "sold"
                           and market not in {line.marketplace for line in own_lines})
        first = min(filter(None, moments), default=None)
        if first:
            first_sales[conv_id] = first
    quantities = _quantities(db, scoped, first_sales)
    live_query = db.query(MarketplaceListing.conversation_id, MarketplaceListing.marketplace, MarketplaceListing.listing_url).filter(
        MarketplaceListing.state.in_(LIVE_STATES))
    if scoped is not None:
        live_query = live_query.filter(MarketplaceListing.conversation_id.in_(scoped))
    direct: dict[str, dict[str, str]] = {}
    observations: dict[str, dict[str, dict]] = {}
    observed = db.query(MarketplaceListing.conversation_id, MarketplaceListing.marketplace, MarketplaceListing.inventory_observation).filter(
        MarketplaceListing.inventory_observation.isnot(None), MarketplaceListing.conversation_id.in_(set(lines)))
    for conv_id, marketplace, observation in observed.all():
        if isinstance(observation, dict):
            observations.setdefault(conv_id, {})[marketplace] = observation
    for conv_id, marketplace, url in live_query.all():
        direct.setdefault(conv_id, {})[marketplace] = url or ""
    result = {}
    for conv_id, notes in conversations:
        vendoo = str(notes.get("vendooStatus") or "")
        vendoo = vendoo if vendoo in VENDOO_STATUSES else "draft"
        quantity = max(1, quantities.get(conv_id, 1))
        own = lines.get(conv_id, [])
        units = sum(_units(line.quantity) for line in own)
        marks = notes.get("marketplaceMarks")
        if not isinstance(marks, dict):
            marks = {}
        with_lines = {line.marketplace for line in own}
        mark_moments = []
        for marketplace, mark in marks.items():
            # A mark made before Mark as sold recorded a sale line.
            if isinstance(mark, dict) and mark.get("state") == "sold" and marketplace not in with_lines:
                units += 1
                mark_moments.append(_time(mark.get("at")))
        sold_out = units > 0 and quantity > 1 and _read_sold_out(observations.get(conv_id, {}), own)
        if vendoo == "sold" or units >= quantity or sold_out:
            # The latest sale Analytics counts, so the Sold tab orders sales as Analytics lists them.
            moments = [sale_moment(line) for line in counted_sales(own, quantity)] if own else mark_moments
            result[conv_id] = ItemStatus("sold", units, quantity, max(filter(None, moments), default=None),
                                         own=vendoo != "sold")
            continue
        sold_at = max(filter(None, [*(sale_moment(line) for line in own), *mark_moments]), default=None)
        live_direct = direct.get(conv_id, {})
        listed_marks = [name for name, mark in marks.items() if isinstance(mark, dict) and mark.get("state") == "listed"]
        removed = set(removed_marketplaces(notes, live_direct))
        vendoo_live = [str(name) for name in notes.get("vendooMarketplaces") or [] if str(name) not in removed] \
            if isinstance(notes.get("vendooMarketplaces"), list) else []
        if live_direct or listed_marks:
            status = "active"
        elif vendoo == "active":
            status = "draft" if removed and not vendoo_live else "active"
        else:
            status = vendoo
        decided = bool(units or live_direct or listed_marks or removed)
        result[conv_id] = ItemStatus(status, units, quantity, sold_at, own=decided)
    return result


def status_for(db: Session, conv_id: str, notes: dict) -> ItemStatus:
    return statuses(db, [(conv_id, notes)])[conv_id]


def sold_units(db: Session, conversations: list) -> dict[str, ItemStatus]:
    """Status for the items with a sale on Anteroom's ledger, so a row can say "1 of 3 sold"."""
    from vendoo_studio.services.vendoo_import import parse_notes

    with_sales = set(paid_lines(db, {conv.id for conv in conversations} if len(conversations) <= 500 else None))
    return statuses(db, [(conv.id, parse_notes(conv.notes)) for conv in conversations if conv.id in with_sales])
