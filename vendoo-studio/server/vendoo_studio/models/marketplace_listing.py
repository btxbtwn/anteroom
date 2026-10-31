"""What Anteroom knows about one item's direct listing on one marketplace.

One row per item and marketplace, written only by the direct List, Update,
Check and Reset paths and by the seller's marks. It used to be a JSON blob in
the conversation's notes, where Clear, Regenerate and any stale copy of the
notes sent back by the editor could erase or roll it back. The marketplace IDs
here are what make a retry reuse the same listing instead of creating another.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.conversation import new_id, utcnow
from vendoo_studio.models.ownership import SellerOwned

# not_saved and draft are resting states the seller can List from; published
# and pending_review are live; the rest mean an action is running or its result
# is unconfirmed.
STATES = ("not_saved", "draft", "saving", "publishing", "updating", "unknown", "published", "pending_review")
BUSY_STATES = ("saving", "publishing", "updating")
UNSETTLED_STATES = (*BUSY_STATES, "unknown")
# Published, or submitted and waiting on the marketplace. Inventory treats both as listed.
LIVE_STATES = ("published", "pending_review")


class MarketplaceListing(SellerOwned, Base):
    __tablename__ = "marketplace_listings"
    __table_args__ = (
        UniqueConstraint("conversation_id", "marketplace", name="uq_marketplace_listings_item"),
        ForeignKeyConstraint(["user_id", "conversation_id"], ["conversations.user_id", "conversations.id"],
            name="fk_marketplace_listings_seller_conversation_id", deferrable=True, initially="DEFERRED"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="not_saved")
    # The last action and the job that ran it. Not a foreign key: Clear keeps
    # the jobs these rows name, and deleting the item deletes both.
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # The id Chrome journals the action under; each step of a List has its own.
    operation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    previous_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    draft_url: Mapped[str | None] = mapped_column(String, nullable=True)
    listing_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # The live listing an Update writes onto.
    listing_id: Mapped[str | None] = mapped_column(String, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_fields: Mapped[object | None] = mapped_column(JSON, nullable=True)
    marketplace_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    ebay_sku: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_offer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_listing_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set before the inventory item is first written: from then on the SKU is this item's.
    ebay_inventory_written: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # [{"photo_id", "checksum", "url"}] in listing order.
    ebay_images: Mapped[object | None] = mapped_column(JSON, nullable=True)
    # A listing with variations: its inventory item group, and {variation SKU: offer ID}.
    ebay_group_key: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_variant_offers: Mapped[object | None] = mapped_column(JSON, nullable=True)

    etsy_shop_id: Mapped[str | None] = mapped_column(String, nullable=True)
    etsy_pending_write: Mapped[str | None] = mapped_column(String, nullable=True)
    etsy_photo_ids: Mapped[object | None] = mapped_column(JSON, nullable=True)
    # {photo_id: listing_image_id}
    etsy_image_ids: Mapped[object | None] = mapped_column(JSON, nullable=True)
    # {photo_id: checksum} of each uploaded file, so an edited photo is uploaded again.
    etsy_image_checksums: Mapped[object | None] = mapped_column(JSON, nullable=True)
    etsy_photos_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    etsy_shipping_profile_id: Mapped[str | None] = mapped_column(String, nullable=True)
    etsy_readiness_state_id: Mapped[str | None] = mapped_column(String, nullable=True)

    storage: Mapped[str | None] = mapped_column(String, nullable=True)
    local_draft_id: Mapped[str | None] = mapped_column(String, nullable=True)
    photo_ids: Mapped[object | None] = mapped_column(JSON, nullable=True)
    sellwild_category_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sellwild_condition_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Latest verified direct inventory read; listing identity and read-start time
    # keep a later sale or replacement link from reusing stale stock.
    inventory_observation: Mapped[object | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


# The fields services read and write as a plain dict.
FIELDS = tuple(
    column.name for column in MarketplaceListing.__table__.columns
    if column.name not in {"id", "conversation_id", "marketplace", "created_at", "updated_at"}
)
