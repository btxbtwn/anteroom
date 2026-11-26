"""The listing API contract that the web app and the iPhone app share.

These models are the single source of truth for the core inventory and
editor endpoints. The hosted routes (``hosted_workspace.py``) and the desktop
routes (``conversations.py``, ``listings.py``) both declare them as
``response_model``, so a response that drifts from the contract fails instead
of reaching a client.

``scripts/api-contract.py`` exports the operations in ``CONTRACT_OPERATIONS``
from the hosted app to ``contracts/anteroom-api.json`` and generates the
TypeScript and Swift models from it. Change a model here, then run that
script; ``tests/test_api_contract.py`` fails while the generated files are
stale or the desktop routes disagree with the hosted ones.

A field with a default may be missing from a response: clients read it as
optional (TypeScript) or fall back to the default (Swift).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from anteroom.services.photo_edits import PhotoEdits
from anteroom.services.photoroom import MAX_BATCH, StudioOptions

# (method, path) of every operation in the shared contract, in the hosted
# app's path spelling. The desktop app serves the same ones (except
# ``/api/runtime``, which only the hosted app has) with the same models.
CONTRACT_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("get", "/api/runtime"),
    ("get", "/api/cloud-sessions"),
    ("post", "/api/cloud-sessions/{marketplace}/verify"),
    ("delete", "/api/cloud-sessions/{marketplace}"),
    ("post", "/api/size-tags/read"),
    ("get", "/api/analytics/offers"),
    ("get", "/api/analytics/offers/log"),
    ("get", "/api/business-expenses"),
    ("post", "/api/business-expenses"),
    ("put", "/api/business-expenses/{expense_id}"),
    ("delete", "/api/business-expenses/{expense_id}"),
    ("post", "/api/business-expenses/mileage"),
    ("post", "/api/business-expenses/import"),
    ("post", "/api/business-expenses/import/preview"),
    ("get", "/api/business-expenses/profit-and-loss"),
    ("get", "/api/business-expenses/plaid/status"),
    ("post", "/api/business-expenses/plaid/link-token"),
    ("post", "/api/business-expenses/plaid/exchange"),
    ("post", "/api/business-expenses/plaid/connections/{connection_id}/sync"),
    ("delete", "/api/business-expenses/plaid/connections/{connection_id}"),
    ("get", "/api/conversations"),
    ("post", "/api/conversations"),
    ("get", "/api/conversations/{identity}"),
    ("patch", "/api/conversations/{identity}"),
    ("delete", "/api/conversations/{identity}"),
    ("put", "/api/conversations/{identity}/favorite"),
    ("get", "/api/conversations/{identity}/listing"),
    ("put", "/api/conversations/{identity}/listing"),
    ("get", "/api/conversations/{identity}/photos"),
    ("get", "/api/listing-sheet"),
    ("post", "/api/conversations/{conv_id}/photos/studio"),
    ("get", "/api/assistant/name"),
    ("put", "/api/assistant/name"),
    ("get", "/api/home"),
    ("post", "/api/home/goals"),
    ("patch", "/api/home/goals/{goal_id}"),
    ("delete", "/api/home/goals/{goal_id}"),
    ("get", "/api/home/attention"),
    ("post", "/api/home/attention/dismiss"),
    ("post", "/api/home/attention/resolve"),
    ("post", "/api/home/attention/restore"),
)
HOSTED_ONLY_OPERATIONS = {
    ("get", "/api/runtime"),
    ("get", "/api/cloud-sessions"),
    ("post", "/api/cloud-sessions/{marketplace}/verify"),
    ("delete", "/api/cloud-sessions/{marketplace}"),
}


class ContractModel(BaseModel):
    # A docstring under a field becomes its description, and a doc comment in both clients.
    model_config = ConfigDict(use_attribute_docstrings=True)


class MarketplaceCloudStatus(ContractModel):
    marketplace: str
    state: str
    mode: str
    cloud_connected: bool
    label: str | None = None
    expires_at: int | None = None
    expiring: bool
    connected_at: str | None = None
    last_used_at: str | None = None
    last_error: str | None = None


class MarketplaceCloudSessions(ContractModel):
    enabled: list[str]
    sessions: dict[str, MarketplaceCloudStatus]


class MarketplaceCloudResult(ContractModel):
    ok: bool
    session: MarketplaceCloudStatus | None = None
    error: str | None = None


class ErrorDetail(ContractModel):
    """What a refused request answers: one sentence for the seller."""

    detail: str


# The error answers a contract operation may give, by status code: what
# either server answers, so a client handles both.
Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "The listing does not exist for this seller."}}
GOAL_NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "The goal does not exist for this seller."}}
CONFLICT: Responses = {409: {"model": ErrorDetail, "description": "The listing changed elsewhere or is busy."}}
REFUSED: Responses = {400: {"model": ErrorDetail, "description": "The change was refused, such as an empty title or a busy listing."}}


class SizeTagReadResult(ContractModel):
    """Clothing size from unsaved tag photos, never the shipping package size."""

    photos_read: int
    status: Literal["accepted", "not_found", "unreadable", "conflict"]
    size: str | None
    """Set only when two independent reads agree; otherwise check the tag. "0" is a size."""
    sizes: list[str]
    """Every distinct size the reads saw, for reviewing a conflict."""


SIZE_TAG_READ_ERRORS: Responses = {
    status: {"model": ErrorDetail, "description": description}
    for status, description in (
        (400, "Too many photos or a photo exceeds the size limit."),
        (500, "The size reading failed; the detail explains what failed."),
        (502, "The photo provider failed or stalled."),
        (503, "No photo model is connected."),
    )
}


WorkspaceFeature = Literal[
    "inventory", "listing_fields", "photos", "preferences", "generation", "assistant", "suggestions",
    "marketplace_actions", "marketplace_sessions", "photo_edits", "photo_background", "chat_attachments",
    "bulk_actions", "bulk_edit", "bulk_copy", "bulk_delete", "bulk_regenerate", "bulk_markdowns",
    "catalog_categories", "ebay_offers", "account_deletion", "managed_ai", "managed_photoroom", "photo_studio",
    "listing_videos",
]


class WorkspaceRuntime(ContractModel):
    """What this hosted server offers the signed-in seller."""

    mode: Literal["hosted"]
    features: list[WorkspaceFeature]
    views: list[str]
    marketplace_forms: list[str]
    """Marketplaces whose native form the editor can show."""
    action_marketplaces: list[str]
    """Marketplaces this server can list on, update and delist from."""


class DirectRecord(ContractModel):
    """Anteroom's own listing of the item on one marketplace."""

    state: str
    """A marketplace_listings state: not_saved, draft, saving, publishing, updating, unknown,
    published or pending_review."""
    listing_url: str | None = None
    failed: bool = False
    """The last List, Update or Save draft failed."""
    fields: int = 0
    """Fields the marketplace rejected and the seller has not edited since."""
    price: float | None = None
    """The live listing's price on this marketplace, when Anteroom knows it."""
    listed_at: str | None = None
    """When Anteroom's last confirmed List put this listing up."""
    inactive: bool = False
    """Still on the marketplace but not for sale (deactivated, expired, Not For Sale), from
    Anteroom's latest verified read of that listing. Neither sold nor deleted."""


class PriceDrift(ContractModel):
    """A marketplace price that is not what the seller's pricing rules give."""

    marketplace: str
    expected: float
    """The price the pricing rules give this marketplace."""
    price: float
    """The price the marketplace has now."""
    source: Literal["override", "live"]
    """``override``: a price typed on that marketplace's form. ``live``: the live listing's known price."""


class ConversationSummary(ContractModel):
    """One inventory row. The sidebar polls the whole inventory, so the seller's
    notes stay out: they are most of the payload and only the open listing reads them."""

    id: str
    title: str | None
    status: str
    scheduled_publish_at: str | None = None
    """When a pending publish is scheduled, in UTC."""
    box_id: str | None = None
    """The wholesale box this item came out of."""
    settled_at: str | None = None
    unsettled_at: str | None = None
    created_at: str
    updated_at: str
    cover_photo_url: str | None = None
    vendoo_status: str | None = None
    """The item's status label in its imported history."""
    vendoo_cover_url: str | None = None
    """The imported image, a thumbnail fallback."""
    sku: str | None = None
    price: float | None = None
    vendoo_labels: list[str] = []
    """The labels the item wears."""
    vendoo_marketplaces: list[str] = []
    """Marketplaces the item was imported as live on."""
    direct_marketplaces: list[str] = []
    """Marketplaces Anteroom published directly."""
    direct_records: dict[str, DirectRecord] = {}
    """Every direct listing record by marketplace, for the inventory table."""
    sold_marketplaces: list[str] = []
    """Marketplaces a sold item sold on, from Anteroom's sales and Mark as sold. Their listing records may still read live."""
    removed_marketplaces: list[str] = []
    """Marketplaces Anteroom delisted directly, though imported history says listed."""
    inactive_marketplaces: list[str] = []
    """Marketplaces where the listing is still up but not for sale. Not a sale and not a delist."""
    price_drift: list[PriceDrift] = []
    """Marketplace prices that differ from the seller's pricing rules; empty while the rules are off."""
    units_sold: int = 0
    """Units sold on Anteroom's sales ledger. An Active row with some sold reads "1 of 3 sold"."""
    quantity: int | None = None
    """The most units the item was offered with."""
    sold_at: str | None = None
    """When Anteroom's sales ledger last saw a unit of this item sell."""
    vendoo_listing_urls: dict[str, str] = {}
    """Each marketplace listing's own page, where the imported history recorded one."""
    vendoo_created_at: str | None = None
    """Imported time tracking: when the item was created."""
    vendoo_modified_at: str | None = None
    """Imported time tracking: when the item was last modified."""
    vendoo_listed_at: str | None = None
    """Imported time tracking: when the item last went live."""
    vendoo_listed_dates: dict[str, str] = {}
    """Imported listing date by marketplace."""
    vendoo_sold_dates: dict[str, str] = {}
    """Imported sale date by marketplace."""
    oldest_listed_at: str | None = None
    """When the item's longest-standing live listing went up, counting Anteroom's own listings:
    what "listed N days ago", the staleness filter and sort read."""
    unsent_edits: bool = False
    """Edited here since the item's live direct listings were written: Update is the step that is owed."""
    etsy_eligible: bool = True
    """False for modern resale: Etsy removes those."""
    missing_sale_data: list[str] = []
    """Sale details the sales ledger still needs from the seller."""
    favorite: bool = False
    """Starred by the seller. Starring is not an edit and leaves ``updated_at`` as it was."""


class Conversation(ConversationSummary):
    """One listing with the seller's notes."""

    notes: str | None


class ConversationInput(ContractModel):
    """A new listing, or the fields of one to change. A field left out stays as it is;
    a null ``box_id`` takes the listing out of its box."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=50000)
    note_changes: dict | None = None
    """Item Details' changed keys, merged into the notes as they are now, so keys
    the server wrote since the notes were read survive. Measurements merge per field."""
    box_id: str | None = Field(None, max_length=100)


class FavoriteInput(ContractModel):
    """Star or unstar one item."""

    model_config = ConfigDict(extra="forbid", strict=True)

    favorite: bool


class FavoriteResult(ContractModel):
    id: str
    favorite: bool


class DeleteConversationResult(ContractModel):
    ok: bool
    deleted_jobs: int
    deleted_photos: int


class ListingPhoto(ContractModel):
    """One photo of a listing. ``url`` carries a content version (``v=``) that
    changes when an edit renders a new file."""

    id: str
    conversation_id: str
    original_filename: str
    mime_type: str
    size_bytes: int
    display_order: int
    width: int | None
    height: int | None
    created_at: str
    url: str
    edits: PhotoEdits | None = None
    """The edits served in place of the kept original; null when unedited."""
    marketplace: str | None = None
    """Set for a photo added on one marketplace's form; only that marketplace can send it."""


class PhotoStudioReview(ContractModel):
    photo_versions: dict[str, str] = Field(min_length=1, max_length=MAX_BATCH)
    """The v query parameter the seller reviewed, keyed by photo ID. Empty for an unversioned photo."""
    scheduled_confirmed_at: str | None = None
    """The confirmation timestamp of the schedule the seller reviewed, if any."""


class PhotoStudioRequest(ContractModel):
    """AI Studio (Photoroom) on one or more of a listing's photos, all with the same options."""

    model_config = ConfigDict(use_attribute_docstrings=True, extra="forbid")

    photo_ids: list[str] = Field(min_length=1, max_length=MAX_BATCH)
    options: StudioOptions
    review: PhotoStudioReview | None = None
    """When present, apply to shared draft photos and enforce the reviewed versions."""


class PhotoStudioFailure(ContractModel):
    photo_id: str
    detail: str
    """Why this photo was not changed, for the seller; the others still saved."""
    needs_review: bool = False
    """The photo or draft changed. Refresh the review before trying again."""


class PhotoStudioResult(ContractModel):
    photos: list[ListingPhoto]
    """The photos AI Studio changed, each with its new ``url`` and ``edits.studio``."""
    errors: list[PhotoStudioFailure]
    provider_blocked: bool = False
    """The key or plan refused the edit. Pause the remaining bulk work."""


PHOTO_STUDIO_ERRORS: Responses = {
    status: {"model": ErrorDetail, "description": description}
    for status, description in (
        (404, "The listing does not exist for this seller."),
        (502, "Photoroom failed or could not be reached."),
        (504, "Photoroom did not answer in time."),
    )
}


class ListingDocument(ContractModel):
    """The listing's current revision, its revision pointer and its validation."""

    conversation_id: str
    current_revision_id: str | None
    """The revision a save must name as ``expected_revision_id``; null before the first save."""
    listing: dict
    revision_count: int
    can_send: bool
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    etsy_eligible: bool = True
    """False for modern resale, which Send leaves off Etsy."""


class ListingSave(ContractModel):
    """A seller's edit, saved only if the listing is still at ``expected_revision_id``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    listing: dict
    expected_revision_id: str = Field(max_length=100)
    """Empty only when the editor has not yet saved a first revision."""


class ListingValidation(ContractModel):
    valid: bool
    errors: list[dict[str, str]]
    warnings: list[dict[str, str]]
    info: list[dict[str, str]]
    can_send: bool


class ListingSaveResult(ContractModel):
    ok: bool
    revision_id: str
    """The new current revision: the next save's ``expected_revision_id``."""
    listing: dict
    etsy_eligible: bool
    validation: ListingValidation


class OfferAnalyticsMarketplace(ContractModel):
    marketplace: str
    confirmed_recipients: int | None
    confirmed_sends: int
    accepted_requests: int
    completed_jobs: int
    unverified: int
    unknown: int
    failed: int
    pending: int


class OfferAnalyticsResponse(ContractModel):
    start: str
    end: str
    timezone: str
    confirmed_conversions: int | None
    marketplaces: list[OfferAnalyticsMarketplace]


class OfferLogEntry(ContractModel):
    id: str
    marketplace: str
    title: str | None
    listing_id: str
    sent_at: str
    status: str
    """sent, sending, unconfirmed (no reply; check the marketplace), checked or failed."""
    price_cents: int | None
    offer_price_cents: int | None
    recipients: int | None
    """Buyers the marketplace said it sent the offer to; null when it did not say."""
    confirmation: str | None
    like_count: int | None
    """Likes (Depop: eligible likers) read before sending; not a recipient count."""
    note: str | None


class OfferLogResponse(ContractModel):
    entries: list[OfferLogEntry]
    next_before: str | None
    """Pass as ``before`` for the next, older page; null on the last page."""


class PreferredName(ContractModel):
    name: str
    """What the seller asked Anteroom to call them; empty until they say. The Assistant home greets them by it."""


class PreferredNameInput(ContractModel):
    name: str = Field(max_length=160)
    """Trimmed and collapsed to single spaces, then at most 40 characters; empty clears it."""


class ListingSheetMissing(ContractModel):
    """One field this marketplace still needs on this listing."""

    field: str
    label: str
    message: str = ""
    rejected: bool = False


class ListingSheetCell(ContractModel):
    """One display cell in a marketplace inventory sheet."""

    value: str = ""
    """Human-readable label for display in the grid or scan list."""
    raw: str = ""
    """Stored scalar value or ID."""
    required: bool = False
    missing: bool = False
    rejected: bool = False
    error: str = ""
    editable: bool = True
    """False when editing requires the full listing editor."""


class ListingSheetRow(ContractModel):
    """One listing's cached cells and readiness for one marketplace."""

    revision_id: str | None = None
    ready: bool = False
    needs_load: bool = False
    """True when this marketplace's full category form is not cached yet."""
    cells: dict[str, ListingSheetCell] = Field(default_factory=dict)
    missing: list[ListingSheetMissing] = Field(default_factory=list)


class ListingSheetResponse(ContractModel):
    """Cached marketplace sheet cells and readiness for the inventory rows on screen."""

    marketplace: Literal["ebay", "poshmark", "mercari", "depop", "etsy"]
    listings: dict[str, ListingSheetRow] = Field(default_factory=dict)



AttentionKind = Literal[
    "still_listed", "expired_session", "failed_job", "unsent_edits", "pending_approval", "relist_gap",
    "duplicate_listing",
]


class AttentionTarget(ContractModel):
    """Where the item's action opens."""

    view: Literal["listings", "settings", "automations"]
    conversation_id: str | None = None
    settings_section: Literal["connections", "listings"] | None = None
    target_id: str | None = None


class AttentionItem(ContractModel):
    """One Needs Attention alert. Snoozing or resolving it only hides it in Anteroom."""

    key: str
    kind: AttentionKind
    priority: Literal["high", "medium", "low"]
    title: str
    detail: str
    action_label: str
    target: AttentionTarget
    occurred_at: str | None = None
    fingerprint: str = ""
    """The state the alert reports. Resolve keeps it hidden only while this stays the same."""
    dismissed_at: str | None = None
    """Set on a snoozed item."""
    expires_at: str | None = None
    """When a snoozed item shows again if it still applies."""
    reappears_in_days: int | None = None
    resolved_at: str | None = None
    """Set on a resolved item."""


class AttentionCounts(ContractModel):
    active: int
    dismissed: int
    resolved: int
    by_kind: dict[str, int] = Field(default_factory=dict)


class AttentionInbox(ContractModel):
    """Needs Attention: what shows, what is snoozed, and what is resolved."""

    needs_attention: list[AttentionItem]
    dismissed: list[AttentionItem]
    """Snoozed items, back after 7 days if they still apply."""
    resolved: list[AttentionItem]
    """Resolved items. A duplicate pair stays resolved; anything else shows again when its state changes."""
    counts: AttentionCounts


class AttentionSnoozeInput(ContractModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)
    item_key: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="general", max_length=40)


class AttentionResolveInput(ContractModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)
    item_key: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="general", max_length=40)
    fingerprint: str = Field(default="", max_length=64)
    """The item's fingerprint as shown; required for every kind but a duplicate pair."""


class AttentionRestoreInput(ContractModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)
    item_key: str = Field(min_length=1, max_length=200)


class AttentionSnooze(ContractModel):
    item_key: str
    dismissed_at: str
    expires_at: str
    reappears_in_days: int


class AttentionSnoozeResult(ContractModel):
    ok: bool
    dismissal: AttentionSnooze


class AttentionResolution(ContractModel):
    item_key: str
    kind: str
    resolved_at: str


class AttentionResolveResult(ContractModel):
    ok: bool
    resolution: AttentionResolution


class AttentionRestoreResult(ContractModel):
    ok: bool
    restored: bool
    item_key: str


GoalPeriod = Literal["weekly", "monthly", "quarterly", "yearly"]
GoalMetric = Literal["profit", "sales", "listings", "sell_through_rate"]


class HomeToday(ContractModel):
    """Today's activity, counted in the seller's time zone."""

    date: str
    timezone: str
    units_listed: int
    drafts_created: int
    units_sold: int
    revenue: float
    profit: float | None = None
    """Null until a sale today has its cost recorded."""
    profit_known: int
    """How many of today's sales have their cost recorded."""
    shares_sent: int
    offers_sent: int
    automation_actions: int


class GoalProgress(ContractModel):
    """One goal and how far the current window is toward it."""

    id: str
    period: GoalPeriod
    metric: GoalMetric
    label: str
    custom_label: str | None = None
    target: float
    current: float
    remaining: float
    progress_percent: float
    expected_percent: float
    """Where the seller would be by now at an even pace."""
    pace: Literal["achieved", "ahead", "on_track", "behind"]
    window_start: str
    window_end: str
    days_elapsed: int
    days_total: int
    days_remaining: int
    unit: Literal["currency", "percent", "count"]
    note: str | None = None


class HomeSummary(AttentionInbox):
    """Today: the day's activity, goals and Needs Attention."""

    today: HomeToday
    goals: list[GoalProgress]


class GoalInput(ContractModel):
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)
    period: GoalPeriod
    metric: GoalMetric
    target: float = Field(gt=0, le=100_000_000)
    label: str | None = Field(default=None, max_length=80)


class GoalUpdate(ContractModel):
    """Only the fields sent change; a label sent as null clears it."""

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)
    period: GoalPeriod | None = None
    metric: GoalMetric | None = None
    target: float | None = Field(default=None, gt=0, le=100_000_000)
    label: str | None = Field(default=None, max_length=80)


class GoalChangeResult(ContractModel):
    ok: bool
    goal_id: str | None = None
    home: HomeSummary | None = None
    """Today after the change; missing when it could not be rebuilt, so the client reloads."""
