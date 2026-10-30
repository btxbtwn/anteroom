"""An item's inventory status from Anteroom's own listings and sales.

Vendoo's synced label (``notes.vendooStatus``) is the imported history. On top
of it, Anteroom's own records decide:

- **Sold** once the units sold reach the item's quantity. A unit is a paid
  line on the sales ledger (a marketplace sale or one the seller recorded with
  Mark as sold), or an older seller mark "sold" on a marketplace with no line
  of its own. A direct stock read showing the listing sold out also counts.
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

    def add(conv_id, value, created_at):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return
        if number < 1:
            return
        first_sale = first_sales.get(conv_id)
        if first_sale is None or _time(created_at) <= first_sale or number > best.get(conv_id, 0):
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


def paid_lines(db: Session, conv_ids: set[str] | None = None) -> dict[str, list[MarketplaceSale]]:
    query = db.query(MarketplaceSale).filter(MarketplaceSale.conversation_id.isnot(None),
                                             MarketplaceSale.order_status.in_(PAID_STATUSES))
    if conv_ids is not None:
        query = query.filter(MarketplaceSale.conversation_id.in_(conv_ids))
    grouped: dict[str, list[MarketplaceSale]] = {}
    for line in query.all():
        grouped.setdefault(line.conversation_id, []).append(line)
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
        moments = [_time(line.sold_at or line.received_at) for line in own]
        marks = notes.get("marketplaceMarks") if isinstance(notes.get("marketplaceMarks"), dict) else {}
        with_lines = {line.marketplace for line in own}
        for marketplace, mark in marks.items():
            # A mark made before Mark as sold recorded a sale line.
            if isinstance(mark, dict) and mark.get("state") == "sold" and marketplace not in with_lines:
                units += 1
                moments.append(_time(mark.get("at")))
        sold_at = max(filter(None, moments), default=None)
        sold_out = units > 0 and quantity > 1 and _read_sold_out(observations.get(conv_id, {}), own)
        if vendoo == "sold" or units >= quantity or sold_out:
            result[conv_id] = ItemStatus("sold", units, quantity, sold_at, own=vendoo != "sold")
            continue
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
