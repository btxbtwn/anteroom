"""Read and write an item's direct marketplace listing records."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from vendoo_studio.models.marketplace_listing import FIELDS, LIVE_STATES, UNSETTLED_STATES, MarketplaceListing


def as_dict(row: MarketplaceListing | None) -> dict:
    """The record as services and the API see it: set fields only, times in ISO form."""
    if row is None:
        return {}
    out = {}
    for name in FIELDS:
        value = getattr(row, name)
        if value is None:
            continue
        if isinstance(value, datetime):
            value = (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
        out[name] = value
    return out


class MarketplaceListingRepo:
    def __init__(self, db: Session):
        self.db = db

    def row(self, conv_id: str, marketplace: str) -> MarketplaceListing | None:
        return (self.db.query(MarketplaceListing)
                .filter(MarketplaceListing.conversation_id == conv_id, MarketplaceListing.marketplace == marketplace)
                .populate_existing().first())

    def get(self, conv_id: str, marketplace: str) -> dict:
        return as_dict(self.row(conv_id, marketplace))

    def for_conversation(self, conv_id: str) -> dict[str, dict]:
        rows = (self.db.query(MarketplaceListing).filter(MarketplaceListing.conversation_id == conv_id)
                .populate_existing().all())
        return {row.marketplace: as_dict(row) for row in rows}

    def save(self, conv_id: str, marketplace: str, values: dict, *, replace: bool = False) -> dict:
        """Write ``values`` onto the record, creating it. ``replace`` clears every field not named."""
        unknown = set(values) - set(FIELDS)
        if unknown:
            raise KeyError(f"Unknown marketplace listing fields: {sorted(unknown)}")
        row = self.row(conv_id, marketplace)
        if row is None:
            row = MarketplaceListing(conversation_id=conv_id, marketplace=marketplace)
            self.db.add(row)
        for name in FIELDS if replace else values:
            value = values.get(name)
            setattr(row, name, value if value is not None or name != "state" else "not_saved")
        self.db.commit()
        return as_dict(row)

    def live_marketplaces(self, conv_id: str | None = None) -> dict[str, list[str]]:
        """Marketplaces each item is live on, from Anteroom's own listing records.

        Vendoo can still say the item is a draft after a direct publish. The
        inventory reads this so that row shows the listing anyway.
        """
        query = self.db.query(MarketplaceListing.conversation_id, MarketplaceListing.marketplace).filter(
            MarketplaceListing.state.in_(LIVE_STATES))
        if conv_id:
            query = query.filter(MarketplaceListing.conversation_id == conv_id)
        grouped: dict[str, list[str]] = {}
        for conversation_id, marketplace in query.all():
            grouped.setdefault(conversation_id, []).append(marketplace)
        return grouped

    def unsettled(self) -> list[MarketplaceListing]:
        """Records whose last action is still running or unconfirmed, on every item."""
        return (self.db.query(MarketplaceListing).filter(MarketplaceListing.state.in_(UNSETTLED_STATES))
                .order_by(MarketplaceListing.updated_at.asc()).all())

    def job_ids(self, conv_id: str) -> set[str]:
        rows = self.db.query(MarketplaceListing.job_id).filter(
            MarketplaceListing.conversation_id == conv_id, MarketplaceListing.job_id.isnot(None))
        return {job_id for (job_id,) in rows}

    def delete_for_conversation(self, conv_id: str) -> None:
        self.db.query(MarketplaceListing).filter(MarketplaceListing.conversation_id == conv_id).delete(synchronize_session=False)
