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

from vendoo_studio.services.photo_edits import PhotoEdits

# (method, path) of every operation in the shared contract, in the hosted
# app's path spelling. The desktop app serves the same ones (except
# ``/api/runtime``, which only the hosted app has) with the same models.
CONTRACT_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("get", "/api/runtime"),
    ("get", "/api/conversations"),
    ("post", "/api/conversations"),
    ("get", "/api/conversations/{identity}"),
    ("patch", "/api/conversations/{identity}"),
    ("delete", "/api/conversations/{identity}"),
    ("get", "/api/conversations/{identity}/listing"),
    ("put", "/api/conversations/{identity}/listing"),
    ("get", "/api/conversations/{identity}/photos"),
)
HOSTED_ONLY_OPERATIONS = {("get", "/api/runtime")}


class ContractModel(BaseModel):
    # A docstring under a field becomes its description, and a doc comment in both clients.
    model_config = ConfigDict(use_attribute_docstrings=True)


class ErrorDetail(ContractModel):
    """What a refused request answers: one sentence for the seller."""

    detail: str


# The error answers a contract operation may give, by status code: what
# either server answers, so a client handles both.
Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": ErrorDetail, "description": "The listing does not exist for this seller."}}
CONFLICT: Responses = {409: {"model": ErrorDetail, "description": "The listing changed elsewhere or is busy."}}
REFUSED: Responses = {400: {"model": ErrorDetail, "description": "The change was refused, such as an empty title or a busy listing."}}


WorkspaceFeature = Literal[
    "inventory", "listing_fields", "photos", "preferences", "generation", "assistant", "suggestions",
    "marketplace_actions", "marketplace_sessions", "photo_edits", "photo_background", "chat_attachments",
    "bulk_actions", "bulk_edit", "bulk_copy", "bulk_delete", "bulk_regenerate", "bulk_markdowns",
    "catalog_categories", "ebay_offers", "account_deletion",
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


class ConversationSummary(ContractModel):
    """One inventory row. The sidebar polls the whole inventory, so the seller's
    notes stay out: they are most of the payload and only the open listing reads them."""

    id: str
    title: str | None
    status: str
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
    removed_marketplaces: list[str] = []
    """Marketplaces Anteroom delisted directly, though imported history says listed."""
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
