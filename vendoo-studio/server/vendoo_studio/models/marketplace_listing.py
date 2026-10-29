"""What Anteroom knows about one item's direct listing on one marketplace.

One row per item and marketplace, written only by the direct List, Update,
Check and Reset paths and by the seller's marks. It used to be a JSON blob in
the conversation's notes, where Clear, Regenerate and any stale copy of the
notes sent back by the editor could erase or roll it back. The marketplace IDs
here are what make a retry reuse the same listing instead of creating another.
"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned
from vendoo_studio.models.conversation import new_id, utcnow

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
    __table_args__ = (UniqueConstraint("conversation_id", "marketplace", name="uq_marketplace_listings_item"),)

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    marketplace = Column(String, nullable=False)
    state = Column(String, nullable=False, default="not_saved")
    # The last action and the job that ran it. Not a foreign key: Clear keeps
    # the jobs these rows name, and deleting the item deletes both.
    action = Column(String, nullable=True)
    job_id = Column(String, nullable=True, index=True)
    # The id Chrome journals the action under; each step of a List has its own.
    operation_id = Column(String, nullable=True)
    revision_id = Column(String, nullable=True)
    previous_revision_id = Column(String, nullable=True)
    draft_url = Column(String, nullable=True)
    listing_url = Column(String, nullable=True)
    # The live listing an Update writes onto.
    listing_id = Column(String, nullable=True)

    error = Column(Text, nullable=True)
    error_code = Column(String, nullable=True)
    error_fields = Column(JSON, nullable=True)
    marketplace_message = Column(Text, nullable=True)
    retry_after = Column(DateTime, nullable=True)

    ebay_sku = Column(String, nullable=True)
    ebay_offer_id = Column(String, nullable=True)
    ebay_listing_id = Column(String, nullable=True)
    # Set before the inventory item is first written: from then on the SKU is this item's.
    ebay_inventory_written = Column(Boolean, nullable=True)
    # [{"photo_id", "checksum", "url"}] in listing order.
    ebay_images = Column(JSON, nullable=True)
    # A listing with variations: its inventory item group, and {variation SKU: offer ID}.
    ebay_group_key = Column(String, nullable=True)
    ebay_variant_offers = Column(JSON, nullable=True)

    etsy_shop_id = Column(String, nullable=True)
    etsy_pending_write = Column(String, nullable=True)
    etsy_photo_ids = Column(JSON, nullable=True)
    # {photo_id: listing_image_id}
    etsy_image_ids = Column(JSON, nullable=True)
    # {photo_id: checksum} of each uploaded file, so an edited photo is uploaded again.
    etsy_image_checksums = Column(JSON, nullable=True)
    etsy_photos_complete = Column(Boolean, nullable=True)
    etsy_shipping_profile_id = Column(String, nullable=True)
    etsy_readiness_state_id = Column(String, nullable=True)

    storage = Column(String, nullable=True)
    local_draft_id = Column(String, nullable=True)
    photo_ids = Column(JSON, nullable=True)
    sellwild_category_id = Column(Integer, nullable=True)
    sellwild_condition_id = Column(Integer, nullable=True)

    # Latest verified direct inventory read; listing identity and read-start time
    # keep a later sale or replacement link from reusing stale stock.
    inventory_observation = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


# The fields services read and write as a plain dict.
FIELDS = tuple(
    column.name for column in MarketplaceListing.__table__.columns
    if column.name not in {"id", "conversation_id", "marketplace", "created_at", "updated_at"}
)
