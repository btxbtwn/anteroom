"""The hosted change feed: when each part of a seller's data last changed.

Other devices subscribe to this table through Supabase Realtime and re-read
the matching screens. A row holds only a topic name and a time, never data.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from vendoo_studio.database import Base
from vendoo_studio.models.ownership import SellerOwned


class SellerChange(SellerOwned, Base):
    __tablename__ = "seller_changes"
    __table_args__ = (PrimaryKeyConstraint("user_id", "topic", name="pk_seller_changes"),)
    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Every seller table names the topic a write to it announces. None means the
# table holds caches, diagnostics or progress that other screens do not show.
# The web client maps the same topic names to the screens it refreshes
# (src/api/liveUpdates.ts).
TOPIC_BY_TABLE: dict[str, str | None] = {
    "conversations": "listings",
    "listings": "listings",
    "listing_revisions": "listings",
    "listing_copy_receipts": "listings",
    "listing_videos": "listings",
    "marketplace_listings": "listings",
    "messages": "listings",
    "similar_item_reviews": "listings",
    "background_tasks": "listings",
    "photos": "photos",
    "jobs": "jobs",
    "job_events": "jobs",
    "fill_log_entries": "jobs",
    "user_preferences": "settings",
    "user_secret_references": "settings",
    "listing_templates": "settings",
    "assistant_threads": "assistant",
    "assistant_messages": "assistant",
    "assistant_memories": "assistant",
    "marketplace_sales": "sales",
    "sale_events": "sales",
    "sale_snapshots": "sales",
    "ad_spend": "sales",
    "automations": "automations",
    "automation_applications": "automations",
    "automation_approvals": "automations",
    "automation_relist_gaps": "automations",
    "automation_runs": "automations",
    "item_price_baselines": "automations",
    "ebay_actions": "automations",
    "ebay_offer_attempts": "automations",
    "ebay_offer_policies": "automations",
    "ebay_offer_rules": "automations",
    "listing_promotions": "automations",
    "marketplace_offer_floors": "automations",
    "promotion_automations": "automations",
    "promotion_runs": "automations",
    "source_boxes": "sourcing",
    "scout_checks": "sourcing",
    # Streamed generation progress: the device running it already polls live.
    "background_task_events": None,
    "category_field_schemas": None,
    "category_nodes": None,
    "category_schemas": None,
    "diagnostic_runs": None,
    "field_observations": None,
    "field_registry": None,
    "generation_timings": None,
    "listing_corrections": None,
    "photo_fingerprints": None,
    "vendoo_draft_cache": None,
    "seller_changes": None,
}
