"""Streamable HTTP Model Context Protocol (MCP) server for Anteroom.

Works identically on both hosted ``myanteroom.com`` (authenticated per seller
via personal access token or seller session with PostgreSQL RLS) and local
Mac desktop.

Safety invariant (AGENTS.md): MCP tools NEVER publish listings, push live
updates to marketplaces, or delist items. All writes remain local drafts or
local saved edits awaiting explicit seller confirmation inside Anteroom.
"""
from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anteroom.config import MAX_PHOTO_COUNT
from anteroom.models.background_task import BackgroundTask
from anteroom.models.conversation import Conversation, Photo
from anteroom.models.job import ACTIVE_JOB_STATUSES, Job
from anteroom.models.listing import Listing, ListingRevision
from anteroom.models.marketplace_listing import LIVE_STATES, MarketplaceListing
from anteroom.models.marketplace_sale import MarketplaceSale
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.repositories.queries import ConversationRepo, ListingRepo, RevisionConflict
from anteroom.services import boxes, home_dashboard, listing_editor, mcp_catalog_tools, mcp_import_match_tools, mcp_tokens
from anteroom.services.background_tasks import ACTIVE as ACTIVE_TASK_STATUSES
from anteroom.services.etsy_audit import etsy_not_allowed_live
from anteroom.services.etsy_fields import etsy_eligible
from anteroom.services.hosted_listing_contract import (
    MARKETS,
    ROOTS as EDITABLE_ROOTS,
    editable_listing,
    remove_photo_selections,
    stored_listing_fields,
)
from anteroom.services.inventory_analytics import inventory_analytics
from anteroom.services.marketplace_delist import removed_marketplaces
from anteroom.services.offer_analytics import offer_analytics, offer_log
from anteroom.services.photos import cover_thumb_url
from anteroom.services.unsent_edits import unsent_edits
from anteroom.services.validation import validate_listing
from anteroom.services.vendoo_import import merge_notes, parse_notes, split_vendoo_labels
from anteroom.version import app_version

SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL_VERSION = "2025-06-18"

SERVER_INSTRUCTIONS = (
    "Anteroom MCP server for reselling inventory, analytics, sourcing ROI, "
    "Needs Attention triage, and safe local draft editing. "
    "IMPORTANT: Never claim an edit was pushed live. All write tools modify "
    "local Anteroom drafts or stage local unsent edits only; the seller "
    "reviews and publishes or delists inside Anteroom."
)

_SPECIFICS_KEYS = frozenset(f"{market}_specifics" for market in MARKETS)
_ALLOWED_MCP_FIELDS = EDITABLE_ROOTS | {"color", "category"}

_READ_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
_DELETE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}


# The permission area and level each tool needs (``services/mcp_tokens.py``).
TOOL_PERMISSIONS: dict[str, tuple[str, str]] = {
    "search_inventory": ("listings", "read"),
    "get_listing": ("listings", "read"),
    "get_listing_photos": ("listings", "read"),
    "create_draft_listing": ("listings", "write"),
    "edit_listing": ("listings", "write"),
    "manage_listing_photos": ("listings", "write"),
    "manage_labels_and_notes": ("listings", "write"),
    "delete_draft_listing": ("listings", "write"),
    "get_sales_analytics": ("analytics", "read"),
    "get_offer_activity": ("analytics", "read"),
    "get_sourcing_roi": ("analytics", "read"),
    "run_etsy_audit": ("analytics", "read"),
    "get_needs_attention": ("attention", "read"),
    "dismiss_attention_items": ("attention", "write"),
    "restore_attention_items": ("attention", "write"),
    **mcp_catalog_tools.PERMISSIONS,
    **mcp_import_match_tools.PERMISSIONS,
}

MAX_ATTENTION_KEYS = 100

# Photos an agent views are downscaled: big enough to read a care tag, small
# enough that a listing's photos fit in one response.
MIN_PHOTO_SIDE = 256
DEFAULT_PHOTO_SIDE = 1024
MAX_PHOTO_SIDE = 1600
# Marks an image item whose bytes are read after the call's database session closes.
_PHOTO_PLACEHOLDER = "_anteroom_photo"

_AREA_NAMES = {"listings": "Listings", "analytics": "Analytics", "attention": "Needs Attention"}
_LEVEL_NAMES = {"read": "Read", "write": "Read and write"}


def _tool_allowed(name: str, permissions: dict[str, str] | None) -> bool:
    """``permissions`` is None for the seller's own session, which reaches every tool."""
    if permissions is None:
        return True
    area, level = TOOL_PERMISSIONS[name]
    return mcp_tokens.allows(permissions, area, level)


class McpToolError(Exception):
    """Expected user-facing tool error surfaced with MCP ``isError: true``."""


def tool_definitions() -> list[dict[str, Any]]:
    """Curated MCP tool catalog exposed to Claude, ChatGPT, Gemini, and Cursor.

    Each title also goes into the annotations, where 2025-03-26 clients read it.
    """
    tools: list[dict[str, Any]] = [
        {
            "name": "search_inventory",
            "title": "Search inventory",
            "description": (
                "Search and filter the seller's Anteroom inventory by keyword "
                "(title, SKU, brand, labels, private notes), status, marketplace, "
                "unsent local edits, or sourcing box."
            ),
            "annotations": _READ_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional text search across title, SKU, brand, labels, and notes."},
                    "status": {"type": "string", "enum": ["draft", "active", "sold"], "description": "Filter by inventory status."},
                    "marketplace": {"type": "string", "enum": sorted(MARKETS), "description": "Filter to items currently listed on this marketplace."},
                    "unsent_edits_only": {"type": "boolean", "description": "If true, return only items with local edits not yet sent to live marketplaces."},
                    "box_id": {"type": "string", "description": "Filter to items belonging to a specific sourcing box ID."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_listing",
            "title": "Get listing details",
            "description": (
                "Inspect full local listing details, fields, marketplace specifics, "
                "validation errors/warnings, Etsy eligibility, live/sold marketplace "
                "status, private notes, labels, and photo metadata/URLs."
            ),
            "annotations": _READ_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                },
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_listing_photos",
            "title": "View listing photos",
            "description": (
                "View a listing's photos as images, in display order, to check its fields "
                "against them (brand and size tags, fabric content, flaws, measurements). "
                "Pass photo_ids to view only some. Read-only."
            ),
            "annotations": _READ_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string", "description": "Listing / conversation ID."},
                    "photo_ids": {
                        "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_PHOTO_COUNT,
                        "description": "Only these photos (IDs from get_listing). Default: every photo.",
                    },
                    "max_side": {
                        "type": "integer", "minimum": MIN_PHOTO_SIDE, "maximum": MAX_PHOTO_SIDE,
                        "default": DEFAULT_PHOTO_SIDE,
                        "description": "Longest side of each returned image in pixels. Raise it to read small tag text.",
                    },
                },
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_sales_analytics",
            "title": "Get sales analytics",
            "description": (
                "Read sales and inventory analytics KPIs (units sold, revenue, profit, "
                "average price, median days to sell, margin, active inventory value, "
                "marketplace breakdown, and aging/stale items)."
            ),
            "annotations": _READ_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "range": {
                        "type": "string",
                        "enum": ["7d", "30d", "90d", "ytd", "all", "custom"],
                        "default": "30d",
                    },
                    "start": {"type": "string", "description": "ISO date YYYY-MM-DD (used when range is custom)."},
                    "end": {"type": "string", "description": "ISO date YYYY-MM-DD (used when range is custom)."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_offer_activity",
            "title": "Get offer activity",
            "description": (
                "Read marketplace offer analytics and recent sent offer log across "
                "eBay, Poshmark, Mercari, and Depop."
            ),
            "annotations": _READ_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "Start ISO date YYYY-MM-DD (defaults to 29 days ago)."},
                    "end": {"type": "string", "description": "End ISO date YYYY-MM-DD (defaults to today UTC)."},
                    "timezone": {"type": "string", "default": "UTC", "description": "IANA timezone name, e.g. America/Chicago."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_sourcing_roi",
            "title": "Get sourcing ROI",
            "description": (
                "Read sourcing box and supplier/store ROI analytics (spend, profit, "
                "ROI, sell-through, average selling price, unsold asking value)."
            ),
            "annotations": _READ_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "store": {"type": "string", "description": "Optional case-insensitive store/supplier filter."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "run_etsy_audit",
            "title": "Audit Etsy listings",
            "description": (
                "Run the read-only Etsy Not-Allowed audit to find unsold Etsy listings "
                "that fail Etsy Creativity Standards eligibility. Never delists."
            ),
            "annotations": _READ_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "get_needs_attention",
            "title": "Get items that need attention",
            "description": (
                "Read the seller's Home Command Center snapshot: today's activity summary, "
                "seller goals progress, and high-priority Needs Attention inbox items "
                "(sold elsewhere/still listed, expired sessions, failed jobs, unsent edits, "
                "pending automation approvals/relist gaps, and potential duplicates)."
            ),
            "annotations": _READ_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "default": "UTC", "description": "IANA timezone name, e.g. America/Chicago."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "dismiss_attention_items",
            "title": "Dismiss attention items",
            "description": (
                "Dismiss Needs Attention items by their key (from get_needs_attention). "
                f"A dismissed item comes back after {home_dashboard.DISMISS_TTL_DAYS} days if it "
                "still applies. Only hides the alert in Anteroom: it never changes a listing "
                "or anything on a marketplace."
            ),
            "annotations": {**_WRITE_ANNOTATIONS, "idempotentHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "item_keys": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 200},
                        "minItems": 1,
                        "maxItems": MAX_ATTENTION_KEYS,
                    },
                },
                "required": ["item_keys"],
                "additionalProperties": False,
            },
        },
        {
            "name": "restore_attention_items",
            "title": "Restore attention items",
            "description": "Bring dismissed Needs Attention items back into the inbox by their key.",
            "annotations": {**_WRITE_ANNOTATIONS, "idempotentHint": True},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "item_keys": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 200},
                        "minItems": 1,
                        "maxItems": MAX_ATTENTION_KEYS,
                    },
                },
                "required": ["item_keys"],
                "additionalProperties": False,
            },
        },
        {
            "name": "create_draft_listing",
            "title": "Create draft listing",
            "description": (
                "Create a new local draft listing in Anteroom. Does NOT publish to any "
                "marketplace; the seller reviews and sends it in Anteroom."
                + mcp_catalog_tools.WRITE_GUIDANCE
            ),
            "annotations": _WRITE_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 200},
                    "description": {"type": "string", "maxLength": 20000},
                    "price": {"type": "number", "minimum": 0},
                    "originalPrice": {"type": "number", "minimum": 0},
                    "cost": {"type": "number", "minimum": 0},
                    "quantity": {"type": "integer", "minimum": 1},
                    "condition": {"type": "string"},
                    "brand": {"type": "string"},
                    "size": {"type": "string"},
                    "primaryColor": {"type": "string"},
                    "secondaryColor": {"type": "string"},
                    "sku": {"type": "string"},
                    "category_path": {"type": "string"},
                    "department": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "material": {"type": "string"},
                    "style": {"type": "string"},
                    "care": {"type": "string"},
                    "flaws": {"type": "string"},
                    "measurements": {"type": "object"},
                    "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
                    "private_notes": {"type": "string", "maxLength": 10000},
                    "box_id": {"type": "string"},
                    "ebay_specifics": {"type": "object"},
                    "poshmark_specifics": {"type": "object"},
                    "mercari_specifics": {"type": "object"},
                    "depop_specifics": {"type": "object"},
                    "etsy_specifics": {"type": "object"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        {
            "name": "edit_listing",
            "title": "Edit listing",
            "description": (
                "Stage local field edits on an existing listing (title, description, "
                "condition, price, quantity, cost, SKU, category, marketplace attributes). "
                "NEVER pushes to live marketplaces; edits on live listings stay as local "
                "unsent edits for the seller to confirm in Anteroom."
                + mcp_catalog_tools.WRITE_GUIDANCE
            ),
            "annotations": _WRITE_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string", "description": "Listing / conversation ID to edit."},
                    "expected_revision_id": {"type": "string", "description": "Optional optimistic concurrency revision ID from get_listing."},
                    "fields": {
                        "type": "object",
                        "description": (
                            "Partial dictionary of editable listing fields to update "
                            "(title, description, condition, price, originalPrice, quantity, "
                            "cost, sku, brand, size, primaryColor, secondaryColor, category_path, "
                            "department, tags, material, style, care, flaws, measurements, "
                            "labels, direct_category_ids, and <marketplace>_specifics)."
                        ),
                    },
                },
                "required": ["conversation_id", "fields"],
                "additionalProperties": False,
            },
        },
        {
            "name": "manage_listing_photos",
            "title": "Manage listing photos",
            "description": (
                "Reorder or remove local photos attached to a listing. "
                "Does not push changes to live marketplaces."
            ),
            "annotations": _WRITE_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["reorder", "remove"]},
                    "ordered_photo_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Complete ordered list of general photo IDs (required when action is 'reorder').",
                    },
                    "photo_id": {
                        "type": "string",
                        "description": "Photo ID to remove (required when action is 'remove').",
                    },
                },
                "required": ["conversation_id", "action"],
                "additionalProperties": False,
            },
        },
        {
            "name": "manage_labels_and_notes",
            "title": "Manage labels and notes",
            "description": (
                "Add, remove, or replace listing labels and update private seller notes "
                "on a listing locally without contacting marketplaces."
            ),
            "annotations": _WRITE_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "set_labels": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
                    "add_labels": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
                    "remove_labels": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
                    "private_notes": {"type": "string", "maxLength": 10000, "description": "Replace private seller notes."},
                    "append_note": {"type": "string", "maxLength": 5000, "description": "Append a new line to private seller notes."},
                },
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "delete_draft_listing",
            "title": "Delete draft listing",
            "description": (
                "Delete an unsent local draft listing. Strictly refuses any listing that "
                "is active, sold, has marketplace records, sales history, or jobs. "
                "Requires confirm=true."
            ),
            "annotations": _DELETE_ANNOTATIONS,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "confirm": {"type": "boolean", "description": "Must be true to delete the unsent local draft."},
                },
                "required": ["conversation_id", "confirm"],
                "additionalProperties": False,
            },
        },
    ]
    tools.extend(mcp_catalog_tools.TOOLS)
    tools.extend(mcp_import_match_tools.TOOLS)
    return [{**tool, "annotations": {**tool["annotations"], "title": tool["title"]}} for tool in tools]


MAX_RPC_BATCH_SIZE = 20


def handle_rpc_payload(
    db: Session,
    payload: Any,
    *,
    session_id: str | None = None,
    protocol_version: str | None = None,
    bearer_token: str | None = None,
    permissions: dict[str, str] | None = None,
    deferred_deletes: list[str] | None = None,
    expired: Callable[[], bool] | None = None,
) -> tuple[Any | None, int, dict[str, str]]:
    """Process a JSON-RPC 2.0 request object or batch.

    ``permissions`` are the calling token's; None means the seller's own
    session (or loopback on the Mac), which reaches every tool.

    Returns ``(response_body_or_none, http_status, extra_headers)``.
    When ``response_body_or_none`` is None (notifications only), HTTP 202 is returned.
    """
    active_version = (
        protocol_version if protocol_version in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
    )
    headers: dict[str, str] = {"MCP-Protocol-Version": active_version}
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    if isinstance(payload, list):
        if not payload:
            return _rpc_error(None, -32600, "Invalid Request: empty batch"), 400, headers
        if len(payload) > MAX_RPC_BATCH_SIZE:
            return (
                _rpc_error(None, -32600, f"Invalid Request: batch exceeds maximum size of {MAX_RPC_BATCH_SIZE}"),
                400,
                headers,
            )
        responses: list[dict[str, Any]] = []
        for item in payload:
            if expired is not None and expired():
                raise TimeoutError
            resp, negotiated = _handle_single(
                db,
                item,
                headers=headers,
                bearer_token=bearer_token,
                permissions=permissions,
                deferred_deletes=deferred_deletes,
            )
            if negotiated:
                headers["MCP-Protocol-Version"] = negotiated
            if resp is not None:
                responses.append(resp)
        if not responses:
            return None, 202, headers
        return responses, 200, headers

    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "Invalid Request: expected JSON object"), 400, headers

    if expired is not None and expired():
        raise TimeoutError
    resp, negotiated = _handle_single(
        db,
        payload,
        headers=headers,
        bearer_token=bearer_token,
        permissions=permissions,
        deferred_deletes=deferred_deletes,
    )
    if negotiated:
        headers["MCP-Protocol-Version"] = negotiated
    if resp is None:
        return None, 202, headers
    return resp, 200, headers


def _handle_single(
    db: Session,
    message: Any,
    *,
    headers: dict[str, str],
    bearer_token: str | None,
    permissions: dict[str, str] | None,
    deferred_deletes: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Invalid Request: jsonrpc must be '2.0'"), None

    method = message.get("method")
    msg_id = message.get("id")
    is_notification = "id" not in message
    params = message.get("params")
    if params is None:
        params = {}

    if not isinstance(method, str) or not method:
        return _rpc_error(msg_id, -32600, "Invalid Request: missing method"), None
    if not isinstance(params, dict):
        return _rpc_error(msg_id, -32602, "Invalid params: expected object"), None

    # Notifications (no id) require no JSON-RPC response body.
    if is_notification or method.startswith("notifications/"):
        return None, None

    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = (
            requested if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        headers.setdefault("Mcp-Session-Id", uuid.uuid4().hex)
        return _rpc_result(msg_id, {
            "protocolVersion": negotiated,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": "anteroom",
                "title": "Anteroom",
                "version": app_version(),
            },
            "instructions": SERVER_INSTRUCTIONS,
        }), negotiated

    if method == "ping":
        return _rpc_result(msg_id, {}), None

    if method == "tools/list":
        tools = [tool for tool in tool_definitions() if _tool_allowed(tool["name"], permissions)]
        return _rpc_result(msg_id, {"tools": tools}), None

    if method == "resources/list":
        return _rpc_result(msg_id, {"resources": []}), None

    if method == "prompts/list":
        return _rpc_result(msg_id, {"prompts": []}), None

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str) or not name:
            return _rpc_error(msg_id, -32602, "Invalid params: tool name is required"), None
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _rpc_error(msg_id, -32602, "Invalid params: arguments must be an object"), None
        if name in TOOL_PERMISSIONS and not _tool_allowed(name, permissions):
            area, level = TOOL_PERMISSIONS[name]
            return _rpc_result(msg_id, _tool_error_result(
                f"This access token is not allowed to use {name}. To allow it, set "
                f"{_AREA_NAMES[area]} to {_LEVEL_NAMES[level]} for this token in "
                "Anteroom Settings → Agents."
            )), None
        try:
            result_data = _dispatch_tool(
                db,
                name,
                arguments,
                bearer_token=bearer_token,
                deferred_deletes=deferred_deletes,
            )
        except KeyError:
            db.rollback()
            return _rpc_error(msg_id, -32602, f"Unknown tool: {name}"), None
        except (McpToolError, ValueError, RevisionConflict) as exc:
            db.rollback()
            return _rpc_result(msg_id, _tool_error_result(str(exc))), None
        except HTTPException as exc:
            db.rollback()
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            return _rpc_result(msg_id, _tool_error_result(detail)), None
        return _rpc_result(msg_id, _tool_success_result(result_data)), None

    return _rpc_error(msg_id, -32601, f"Method not found: {method}"), None


def _rpc_result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_success_result(data: dict[str, Any]) -> dict[str, Any]:
    # Content filled in once the session is closed: photos, and import links to check.
    images = [*data.pop("_images", []), *data.pop("_after", [])]
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}, *images],
        "isError": False,
    }


def _tool_error_result(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _dispatch_tool(
    db: Session,
    name: str,
    args: dict[str, Any],
    *,
    bearer_token: str | None,
    deferred_deletes: list[str] | None = None,
) -> dict[str, Any]:
    handlers = {
        "search_inventory": _tool_search_inventory,
        "get_listing": _tool_get_listing,
        "get_listing_photos": _tool_get_listing_photos,
        "get_sales_analytics": _tool_get_sales_analytics,
        "get_offer_activity": _tool_get_offer_activity,
        "get_sourcing_roi": _tool_get_sourcing_roi,
        "run_etsy_audit": _tool_run_etsy_audit,
        "get_needs_attention": _tool_get_needs_attention,
        "dismiss_attention_items": _tool_dismiss_attention_items,
        "restore_attention_items": _tool_restore_attention_items,
        "create_draft_listing": _tool_create_draft_listing,
        "edit_listing": _tool_edit_listing,
        "manage_listing_photos": _tool_manage_listing_photos,
        "manage_labels_and_notes": _tool_manage_labels_and_notes,
        "delete_draft_listing": _tool_delete_draft_listing,
        **mcp_catalog_tools.HANDLERS,
        **mcp_import_match_tools.HANDLERS,
    }
    handler = handlers.get(name)
    if handler is None:
        raise KeyError(name)
    return handler(db, args, bearer_token=bearer_token, deferred_deletes=deferred_deletes)


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------


def _tool_search_inventory(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    query_text = str(args.get("query") or "").strip().casefold()
    status_filter = str(args.get("status") or "").strip().lower() or None
    if status_filter and status_filter not in {"draft", "active", "sold"}:
        raise McpToolError("status must be one of: draft, active, sold.")
    marketplace_filter = str(args.get("marketplace") or "").strip().lower() or None
    if marketplace_filter and marketplace_filter not in MARKETS:
        raise McpToolError(f"marketplace must be one of: {', '.join(sorted(MARKETS))}.")
    unsent_only = bool(args.get("unsent_edits_only"))
    box_id = str(args.get("box_id") or "").strip() or None
    limit = _bounded_int(args.get("limit"), default=25, minimum=1, maximum=100)
    offset = _bounded_int(args.get("offset"), default=0, minimum=0, maximum=10000)

    convs = list(
        db.scalars(
            select(Conversation).order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        ).all()
    )
    if status_filter:
        convs = [c for c in convs if c.status == status_filter]
    if box_id:
        convs = [c for c in convs if c.box_id == box_id]

    revisions_by_conv: dict[str, str] = {}
    fields_by_conv: dict[str, dict[str, Any]] = {}
    for conv_id, rev_id, doc in db.execute(
        select(Listing.conversation_id, Listing.current_revision_id, ListingRevision.listing_json)
        .outerjoin(ListingRevision, ListingRevision.id == Listing.current_revision_id)
    ).all():
        if rev_id:
            revisions_by_conv[conv_id] = rev_id
        if isinstance(doc, dict):
            fields_by_conv[conv_id] = doc

    direct_summaries = MarketplaceListingRepo(db).summaries()
    unsent_ids = unsent_edits(db, convs, revisions_by_conv, commit_backfill=False)

    photo_counts: dict[str, int] = {}
    for conv_id in db.scalars(select(Photo.conversation_id).where(Photo.marketplace.is_(None))).all():
        photo_counts[conv_id] = photo_counts.get(conv_id, 0) + 1

    matched: list[dict[str, Any]] = []
    for conv in convs:
        doc = fields_by_conv.get(conv.id) or {}
        notes = parse_notes(conv.notes)
        labels = list(doc.get("labels") or []) if isinstance(doc.get("labels"), list) else split_vendoo_labels(notes.get("vendooLabels"))
        direct = direct_summaries.get(conv.id) or {}
        sold = {m for m, rec in direct.items() if rec.get("state") == "sold"} | {
            str(m).lower()
            for m, when in ((notes.get("vendooDates") or {}).get("soldByMarketplace") or {}).items()
            if when
        }
        # As the web app reads it: the import's marketplaces less the ones Anteroom
        # delisted or the seller marked not listed, plus Anteroom's own live
        # listings, less the marketplaces the item sold on.
        removed = set(removed_marketplaces(notes, {
            m: str(rec["listing_url"]) for m, rec in direct.items() if rec.get("listing_url")}))
        imported = {str(m).lower() for m in (notes.get("vendooMarketplaces") or []) if isinstance(m, str)}
        live_markets = sorted(
            ({m for m, rec in direct.items() if rec.get("state") in LIVE_STATES} | (imported - removed)) - sold
        )
        sold_markets = sorted(sold)
        has_unsent = conv.id in unsent_ids

        if marketplace_filter and marketplace_filter not in live_markets:
            continue
        if unsent_only and not has_unsent:
            continue

        title = str(doc.get("title") or conv.title or "Untitled")
        sku = str(doc.get("sku") or notes.get("sku") or "")
        brand = str(doc.get("brand") or "")
        seller_notes = str(notes.get("sellerNotes") or "")
        if query_text:
            haystack = " ".join([title, sku, brand, " ".join(labels), seller_notes]).casefold()
            if query_text not in haystack:
                continue

        matched.append({
            "conversation_id": conv.id,
            "title": title,
            "status": conv.status,
            "price": doc.get("price"),
            "cost": doc.get("cost"),
            "quantity": doc.get("quantity"),
            "sku": sku or None,
            "brand": brand or None,
            "condition": doc.get("condition") or None,
            "labels": labels,
            "live_marketplaces": live_markets,
            "sold_marketplaces": sold_markets,
            "unsent_edits": has_unsent,
            "photo_count": photo_counts.get(conv.id, 0),
            "box_id": conv.box_id,
            "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        })

    page = matched[offset : offset + limit]
    return {
        "total": len(matched),
        "offset": offset,
        "limit": limit,
        "items": page,
    }


def _tool_get_listing(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        raise McpToolError("Listing not found.")

    listing_repo = ListingRepo(db)
    current = listing_repo.get_current(conv_id)
    revision = listing_repo.get_revision(current.current_revision_id) if current and current.current_revision_id else None
    raw_doc = revision.listing_json if revision and isinstance(revision.listing_json, dict) else {}
    editable = stored_listing_fields(raw_doc) if raw_doc else {}
    if not editable.get("title"):
        editable["title"] = conv.title or ""

    photos = db.scalars(
        select(Photo)
        .where(Photo.conversation_id == conv_id)
        .order_by(Photo.display_order, Photo.created_at, Photo.id)
    ).all()
    photo_payload = [
        {
            "id": p.id,
            "display_order": p.display_order,
            "marketplace": p.marketplace,
            "width": p.width,
            "height": p.height,
            "size_bytes": p.size_bytes,
            "mime_type": p.mime_type,
            "url": f"/api/photos/{p.id}",
            "thumb_url": cover_thumb_url(p.id, p.checksum),
        }
        for p in photos
    ]

    validation = validate_listing(copy.deepcopy(editable), len(photos), require_photos=True)
    notes = parse_notes(conv.notes)
    labels = editable.get("labels") or split_vendoo_labels(notes.get("vendooLabels"))
    direct = MarketplaceListingRepo(db).summaries().get(conv_id, {})
    revisions_map = {conv_id: revision.id} if revision else {}
    has_unsent = conv_id in unsent_edits(db, [conv], revisions_map, commit_backfill=False)

    return {
        "conversation_id": conv.id,
        "status": conv.status,
        "current_revision_id": revision.id if revision else None,
        "box_id": conv.box_id,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        "unsent_edits": has_unsent,
        "labels": labels,
        "private_notes": str(notes.get("sellerNotes") or ""),
        "listing": editable,
        "validation": {
            "valid": validation.valid,
            "can_send": validation.can_send,
            "errors": validation.errors,
            "warnings": validation.warnings,
        },
        "etsy_eligible": etsy_eligible(editable),
        "marketplaces": direct,
        "photos": photo_payload,
    }


def _tool_get_listing_photos(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    if ConversationRepo(db).get(conv_id) is None:
        raise McpToolError("Listing not found.")
    max_side = _bounded_int(
        args.get("max_side"), default=DEFAULT_PHOTO_SIDE, minimum=MIN_PHOTO_SIDE, maximum=MAX_PHOTO_SIDE,
    )
    photos = db.scalars(
        select(Photo)
        .where(Photo.conversation_id == conv_id)
        .order_by(Photo.display_order, Photo.created_at, Photo.id)
    ).all()
    wanted = args.get("photo_ids")
    if wanted is not None:
        if (not isinstance(wanted, list) or not wanted or len(wanted) > MAX_PHOTO_COUNT
                or not all(isinstance(i, str) for i in wanted)):
            raise McpToolError(f"photo_ids must be a list of 1 to {MAX_PHOTO_COUNT} photo IDs.")
        unknown = sorted(set(wanted) - {p.id for p in photos})
        if unknown:
            raise McpToolError(f"Photo not found on this listing: {', '.join(unknown)}.")
        photos = [p for p in photos if p.id in set(wanted)]
    return {
        "conversation_id": conv_id,
        "photos": [
            {"id": p.id, "display_order": p.display_order, "marketplace": p.marketplace}
            for p in photos
        ],
        "pushed_to_marketplaces": False,
        "_images": [_photo_placeholder(p.id, p.stored_filename, max_side) for p in photos],
    }


def _photo_placeholder(photo_id: str, key: str, max_side: int) -> dict[str, Any]:
    return {"type": "image", "mimeType": "image/jpeg", "data": "",
            _PHOTO_PLACEHOLDER: {"id": photo_id, "key": key, "max_side": max_side}}


def pending_photos(body: Any) -> list[tuple[list[Any], int]]:
    """Where a response still holds photo placeholders, as (content list, index) pairs."""
    responses = body if isinstance(body, list) else [body]
    found = []
    for response in responses:
        result = response.get("result") if isinstance(response, dict) else None
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list):
            found.extend(
                (content, index) for index, item in enumerate(content)
                if isinstance(item, dict) and _PHOTO_PLACEHOLDER in item
            )
    return found


def load_pending_photos(pending: list[tuple[list[Any], int]], *, deadline: float) -> None:
    """Replace each placeholder with its downscaled JPEG, or a line naming a photo that failed.

    Runs after the call's database session is closed: photo bytes may come from
    Storage over the network. ``deadline`` is a ``time.monotonic()`` value.
    """
    import base64
    import time

    from anteroom.services import image_decode

    for content, index in pending:
        photo = content[index][_PHOTO_PLACEHOLDER]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            content[index] = {"type": "text", "text": f"Photo {photo['id']} was not loaded in time. Ask for it again."}
            continue
        try:
            # Mac uploads keep the camera's bytes, EXIF orientation included.
            jpeg = image_decode.jpeg(_read_photo(photo["key"], timeout=remaining),
                                     max_side=photo["max_side"], quality=82)
        except Exception:  # noqa: BLE001 - one unreadable photo must not fail the others
            content[index] = {"type": "text", "text": f"Photo {photo['id']} could not be loaded."}
            continue
        content[index] = {"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii")}


def _read_photo(key: str, *, timeout: float) -> bytes:
    from anteroom.services.hosted_context import HostedPhotoStore
    from anteroom.services.photo_store import photo_store

    store = photo_store()
    if not isinstance(store, HostedPhotoStore):
        return store.read(key)
    from anteroom.services import hosted_photos, hosted_storage

    token = store.storage_token()
    # A token Storage refuses (an MCP access token or a connected app's) reads
    # on the server key, which checks the key belongs to this seller.
    if token is None:
        return hosted_storage.photo_call("GET", key, timeout=min(timeout, 30.0))
    return hosted_photos.storage_call("GET", key, token, timeout=min(timeout, 20.0))


def _tool_get_sales_analytics(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    range_id = str(args.get("range") or "30d").strip().lower()
    start = _parse_optional_date(args.get("start"), "start")
    end = _parse_optional_date(args.get("end"), "end")
    try:
        payload = inventory_analytics(db, range_id=range_id, start=start, end=end)
    except ValueError as exc:
        raise McpToolError(str(exc)) from exc
    # Keep MCP output focused and within comfortable LLM context sizes.
    return {
        "range": payload.get("range"),
        "start": payload.get("start"),
        "end": payload.get("end"),
        "inventory": payload.get("inventory"),
        "sales": payload.get("sales"),
        "previous": payload.get("previous"),
        "marketplaces": payload.get("marketplaces"),
        "stale_top": (payload.get("stale") or [])[:15],
        "recent_sales": (payload.get("recent") or [])[:20],
    }


def _tool_get_offer_activity(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    end = _parse_optional_date(args.get("end"), "end") or today
    start = _parse_optional_date(args.get("start"), "start") or (end - timedelta(days=29))
    tz = str(args.get("timezone") or "UTC").strip() or "UTC"
    limit = _bounded_int(args.get("limit"), default=25, minimum=1, maximum=100)
    try:
        summary = offer_analytics(db, start=start, end=end, timezone=tz)
        log = offer_log(db, limit=limit)
    except Exception as exc:
        raise McpToolError(f"Could not read offer activity: {exc}") from exc
    return {
        "summary": summary,
        "recent_offers": log,
    }


def _tool_get_sourcing_roi(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    store_filter = str(args.get("store") or "").strip().casefold()
    limit = _bounded_int(args.get("limit"), default=50, minimum=1, maximum=100)
    data = boxes.results(db)
    box_rows = data.get("boxes") or []
    store_rows = data.get("stores") or []
    if store_filter:
        box_rows = [row for row in box_rows if store_filter in str(row.get("store") or "").casefold()]
        store_rows = [row for row in store_rows if store_filter in str(row.get("store") or "").casefold()]
    return {
        "store_count": len(store_rows),
        "box_count": len(box_rows),
        "stores": store_rows[:limit],
        "boxes": box_rows[:limit],
    }


def _tool_run_etsy_audit(db: Session, _args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    items = etsy_not_allowed_live(db)
    return {
        "count": len(items),
        "note": "Read-only audit. Review and delist any ineligible Etsy listings inside Anteroom.",
        "items": items,
    }


def _tool_get_needs_attention(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    tz = str(args.get("timezone") or "UTC").strip() or "UTC"
    return home_dashboard.home_summary(db, tz_name=tz)


def _attention_keys(args: dict[str, Any]) -> list[str]:
    raw = args.get("item_keys")
    if not isinstance(raw, list) or not raw:
        raise McpToolError("item_keys must list at least one Needs Attention item key.")
    if len(raw) > MAX_ATTENTION_KEYS:
        raise McpToolError(f"Send at most {MAX_ATTENTION_KEYS} item keys at a time.")
    keys: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
            raise McpToolError("Each item key must be a non-empty string of at most 200 characters.")
        if value.strip() not in keys:
            keys.append(value.strip())
    return keys


def _tool_dismiss_attention_items(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    keys = _attention_keys(args)
    kinds = home_dashboard.attention_item_kinds(db)
    dismissed: list[dict[str, Any]] = []
    not_found: list[str] = []
    for key in keys:
        if key not in kinds:
            not_found.append(key)
            continue
        dismissed.append(home_dashboard.dismiss_attention_item(db, item_key=key, kind=kinds[key]))
    return {"dismissed": dismissed, "not_found": not_found}


def _tool_restore_attention_items(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    keys = _attention_keys(args)
    restored = [key for key in keys if home_dashboard.restore_attention_item(db, item_key=key)]
    return {"restored": restored, "not_dismissed": [key for key in keys if key not in restored]}


# ---------------------------------------------------------------------------
# Safe local write tools (NEVER publish, update live marketplaces, or delist)
# ---------------------------------------------------------------------------


def _normalize_incoming_fields(raw_fields: dict[str, Any]) -> dict[str, Any]:
    """Map convenient aliases and validate against the seller-editable contract."""
    mapped: dict[str, Any] = {}
    for key, value in raw_fields.items():
        if key not in _ALLOWED_MCP_FIELDS:
            raise McpToolError(f"Unsupported or operational field: {key}")
        target_key = "primaryColor" if key == "color" else "category_path" if key == "category" else key
        mapped[target_key] = value
    return mapped


def _tool_create_draft_listing(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    title = _require_str(args, "title", max_len=200)
    private_notes = args.get("private_notes")
    box_id = str(args.get("box_id") or "").strip() or None
    if box_id is not None:
        from anteroom.models.sourcing import SourceBox

        if db.get(SourceBox, box_id) is None:
            raise McpToolError("Sourcing box not found.")

    raw_fields = {k: v for k, v in args.items() if k not in {"private_notes", "box_id"}}
    listing_data = _normalize_incoming_fields(raw_fields)
    listing_data["title"] = title
    editable_listing(listing_data)

    initial_notes_dict: dict[str, Any] = {}
    if isinstance(private_notes, str) and private_notes.strip():
        initial_notes_dict["sellerNotes"] = private_notes.strip()
    if isinstance(listing_data.get("labels"), list) and listing_data["labels"]:
        initial_notes_dict["vendooLabels"] = ", ".join(
            str(label).strip() for label in listing_data["labels"] if str(label).strip()
        )
    notes_str = json.dumps(initial_notes_dict) if initial_notes_dict else None

    conv = Conversation(title=title, notes=notes_str, box_id=box_id, status="draft")
    db.add(conv)
    db.flush()

    saved = listing_editor.save_listing_edit(conv.id, listing_data, db, source="mcp", commit=True)
    return {
        "ok": True,
        "conversation_id": conv.id,
        "status": "draft",
        "revision_id": saved["revision_id"],
        "listing": saved["listing"],
        "validation": saved["validation"],
        "published": False,
        "note": "Created as a local draft only. Review and publish inside Anteroom.",
    }


def _tool_edit_listing(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    raw_fields = args.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise McpToolError("fields must be a non-empty object of listing fields to update.")
    expected_revision_id = args.get("expected_revision_id")
    if expected_revision_id is not None and not isinstance(expected_revision_id, str):
        raise McpToolError("expected_revision_id must be a string.")

    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        raise McpToolError("Listing not found.")
    _assert_not_busy(db, conv_id)

    updates = _normalize_incoming_fields(raw_fields)

    listing_repo = ListingRepo(db)
    current = listing_repo.get_current(conv_id)
    revision = listing_repo.get_revision(current.current_revision_id) if current and current.current_revision_id else None
    merged = copy.deepcopy(revision.listing_json) if revision and isinstance(revision.listing_json, dict) else {}
    if not merged.get("title") and conv.title:
        merged["title"] = conv.title

    for key, value in updates.items():
        if key in _SPECIFICS_KEYS and isinstance(value, dict) and isinstance(merged.get(key), dict):
            combined = copy.deepcopy(merged[key])
            combined.update(value)
            merged[key] = combined
        else:
            merged[key] = value

    # Validate the seller-editable projection before passing to listing_editor.
    editable_listing(stored_listing_fields(merged))

    if expected_revision_id is None:
        expected_revision_id = revision.id if revision else ""
    saved = listing_editor.save_listing_edit(
        conv_id,
        merged,
        db,
        source="mcp",
        expected_revision_id=expected_revision_id,
        commit=False,
    )
    new_title = str(saved["listing"].get("title") or "").strip()
    if new_title and new_title != conv.title:
        conv.title = new_title
    db.commit()

    has_live = bool(
        db.scalar(
            select(MarketplaceListing.id)
            .where(MarketplaceListing.conversation_id == conv_id, MarketplaceListing.state.in_(LIVE_STATES))
            .limit(1)
        )
    )
    return {
        "ok": True,
        "conversation_id": conv_id,
        "revision_id": saved["revision_id"],
        "listing": stored_listing_fields(saved["listing"]),
        "validation": saved["validation"],
        "pushed_to_marketplaces": False,
        "unsent_live_edit": has_live,
        "note": (
            "Saved locally as an unsent edit. Live marketplaces were NOT updated; "
            "review and send from Anteroom."
            if has_live
            else "Saved locally to draft. Nothing was published."
        ),
    }


def _tool_manage_listing_photos(
    db: Session,
    args: dict[str, Any],
    *,
    bearer_token: str | None = None,
    deferred_deletes: list[str] | None = None,
) -> dict[str, Any]:
    from anteroom.services.draft_scheduling import unschedule_on_edit

    conv_id = _require_str(args, "conversation_id")
    action = str(args.get("action") or "").strip().lower()
    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        raise McpToolError("Listing not found.")
    _assert_not_busy(db, conv_id)

    if action == "reorder":
        ordered_ids = args.get("ordered_photo_ids")
        if not isinstance(ordered_ids, list) or not ordered_ids or not all(isinstance(i, str) for i in ordered_ids):
            raise McpToolError("ordered_photo_ids must be a non-empty list of photo IDs.")
        rows = db.scalars(
            select(Photo).where(Photo.conversation_id == conv_id, Photo.marketplace.is_(None))
        ).all()
        if len(set(ordered_ids)) != len(ordered_ids) or set(ordered_ids) != {r.id for r in rows}:
            raise McpToolError("ordered_photo_ids must include every general photo on this listing exactly once.")
        positions = {pid: idx for idx, pid in enumerate(ordered_ids)}
        for row in rows:
            row.display_order = positions[row.id]
        unschedule_on_edit(db, conv_id, reason="Photos changed after scheduling; review and reschedule before publishing.")
        db.commit()
        return {
            "ok": True,
            "conversation_id": conv_id,
            "action": "reorder",
            "ordered_photo_ids": ordered_ids,
            "pushed_to_marketplaces": False,
        }

    if action == "remove":
        photo_id = _require_str(args, "photo_id")
        photo = db.scalar(
            select(Photo).where(Photo.id == photo_id, Photo.conversation_id == conv_id)
        )
        if photo is None:
            raise McpToolError("Photo not found on this listing.")
        stored_key = photo.stored_filename
        source_key = photo.source_filename
        remove_photo_selections(db, conv_id, photo_id)
        db.delete(photo)
        unschedule_on_edit(db, conv_id, reason="Photos changed after scheduling; review and reschedule before publishing.")
        db.commit()
        _best_effort_delete_photo_files(
            db, stored_key, source_key, bearer_token=bearer_token, deferred_deletes=deferred_deletes,
        )
        remaining = db.scalars(
            select(Photo.id)
            .where(Photo.conversation_id == conv_id)
            .order_by(Photo.display_order, Photo.created_at, Photo.id)
        ).all()
        return {
            "ok": True,
            "conversation_id": conv_id,
            "action": "remove",
            "removed_photo_id": photo_id,
            "remaining_photo_ids": list(remaining),
            "pushed_to_marketplaces": False,
        }

    raise McpToolError("action must be 'reorder' or 'remove'.")


def _tool_manage_labels_and_notes(db: Session, args: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    conv = ConversationRepo(db).get(conv_id)
    if conv is None:
        raise McpToolError("Listing not found.")
    _assert_not_busy(db, conv_id)

    notes = parse_notes(conv.notes)
    listing_repo = ListingRepo(db)
    current = listing_repo.get_current(conv_id)
    revision = listing_repo.get_revision(current.current_revision_id) if current and current.current_revision_id else None
    doc = copy.deepcopy(revision.listing_json) if revision and isinstance(revision.listing_json, dict) else {}

    existing_labels = (
        [str(x).strip() for x in doc["labels"] if str(x).strip()]
        if isinstance(doc.get("labels"), list)
        else split_vendoo_labels(notes.get("vendooLabels"))
    )
    updated_labels = list(existing_labels)
    labels_changed = False

    if "set_labels" in args and args["set_labels"] is not None:
        if not isinstance(args["set_labels"], list):
            raise McpToolError("set_labels must be a list of strings.")
        updated_labels = _dedupe_labels(args["set_labels"])
        labels_changed = True
    if "add_labels" in args and args["add_labels"] is not None:
        if not isinstance(args["add_labels"], list):
            raise McpToolError("add_labels must be a list of strings.")
        updated_labels = _dedupe_labels(updated_labels + list(args["add_labels"]))
        labels_changed = True
    if "remove_labels" in args and args["remove_labels"] is not None:
        if not isinstance(args["remove_labels"], list):
            raise McpToolError("remove_labels must be a list of strings.")
        remove_set = {str(x).strip().casefold() for x in args["remove_labels"]}
        updated_labels = [lbl for lbl in updated_labels if lbl.casefold() not in remove_set]
        labels_changed = True

    if len(updated_labels) > 30:
        raise McpToolError("A listing may have at most 30 labels.")

    existing_note = str(notes.get("sellerNotes") or "")
    new_note = existing_note
    note_changed = False
    if "private_notes" in args and args["private_notes"] is not None:
        if not isinstance(args["private_notes"], str):
            raise McpToolError("private_notes must be a string.")
        new_note = args["private_notes"].strip()
        note_changed = True
    if "append_note" in args and args["append_note"] is not None:
        if not isinstance(args["append_note"], str):
            raise McpToolError("append_note must be a string.")
        addition = args["append_note"].strip()
        if addition:
            new_note = f"{new_note}\n{addition}".strip() if new_note else addition
            note_changed = True

    if not labels_changed and not note_changed:
        raise McpToolError("Provide at least one label or private_notes change.")

    note_updates: dict[str, Any] = {}
    if labels_changed:
        note_updates["vendooLabels"] = ", ".join(updated_labels)
    if note_changed:
        note_updates["sellerNotes"] = new_note

    if labels_changed and revision is not None:
        doc["labels"] = updated_labels
        listing_editor.save_listing_edit(
            conv_id, doc, db, source="mcp", expected_revision_id=revision.id, commit=False,
        )

    conv.notes = merge_notes(conv.notes, note_updates)
    db.commit()

    return {
        "ok": True,
        "conversation_id": conv_id,
        "labels": updated_labels,
        "private_notes": new_note,
        "pushed_to_marketplaces": False,
    }


def _tool_delete_draft_listing(
    db: Session,
    args: dict[str, Any],
    *,
    bearer_token: str | None = None,
    deferred_deletes: list[str] | None = None,
) -> dict[str, Any]:
    conv_id = _require_str(args, "conversation_id")
    if args.get("confirm") is not True:
        raise McpToolError("Set confirm=true to delete this unsent local draft.")

    hosted = hasattr(db, "user_id")
    # Hosted task submission takes the same lock. Keep it through the idle
    # check and commit so deletion cannot erase newly accepted work.
    conv = (
        db.get(Conversation, conv_id, populate_existing=True, with_for_update=True)
        if hosted else ConversationRepo(db).get(conv_id)
    )
    if conv is None:
        raise McpToolError("Listing not found.")

    _assert_not_busy(db, conv_id)

    notes = parse_notes(conv.notes)
    has_vendoo_markets = bool(notes.get("vendooMarketplaces"))
    has_marketplace_rows = bool(
        db.scalar(select(MarketplaceListing.id).where(MarketplaceListing.conversation_id == conv_id).limit(1))
    )
    has_jobs = bool(db.scalar(select(Job.id).where(Job.conversation_id == conv_id).limit(1)))
    has_sales = bool(db.scalar(select(MarketplaceSale.id).where(MarketplaceSale.conversation_id == conv_id).limit(1)))

    if conv.status != "draft" or has_marketplace_rows or has_vendoo_markets or has_jobs or has_sales:
        raise McpToolError(
            "Refused: only unsent local drafts with no marketplace records, jobs, or sales can be deleted via MCP."
        )

    if hosted:
        from anteroom.services import hosted_listing_delete

        deleted_photos = db.scalar(select(func.count()).select_from(Photo).where(Photo.conversation_id == conv_id))
        storage_keys = hosted_listing_delete.remove_unsent_draft(db, conv_id)
        db.commit()
        for key in storage_keys:
            _best_effort_delete_photo_files(
                db, key, None, bearer_token=bearer_token, deferred_deletes=deferred_deletes,
            )
    else:
        from anteroom.services.listing_delete import ListingBusy, delete_listing

        try:
            _, deleted_photos = delete_listing(db, conv_id)
        except (ListingBusy, LookupError) as exc:
            raise McpToolError(str(exc)) from exc

    return {
        "ok": True,
        "conversation_id": conv_id,
        "deleted": True,
        "deleted_photos": deleted_photos,
        "delisted_from_marketplaces": False,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_not_busy(db: Session, conv_id: str) -> None:
    if db.scalar(
        select(Job.id).where(Job.conversation_id == conv_id, Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)
    ):
        raise McpToolError("Wait for the active listing job to finish before editing.")
    if db.scalar(
        select(BackgroundTask.id)
        .where(BackgroundTask.conversation_id == conv_id, BackgroundTask.status.in_(ACTIVE_TASK_STATUSES))
        .limit(1)
    ):
        raise McpToolError("Wait for the active background task to finish before editing.")


def delete_hosted_storage_keys(
    keys: list[str],
    *,
    bearer_token: str | None,
    deadline_seconds: float = 15.0,
    server_key: bool = False,
) -> None:
    """Best-effort delete of hosted Storage objects outside any DB session, bounded by a deadline.

    ``server_key`` is for callers whose token Storage refuses: a connected app's
    (Claude, ChatGPT, Gemini) token never reaches Storage directly.
    """
    import time

    from anteroom.services import hosted_photos, hosted_storage

    if not keys:
        return
    deadline = time.monotonic() + deadline_seconds
    for key in dict.fromkeys(k for k in keys if k):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            if server_key or (bearer_token and bearer_token.startswith("arm_")):
                hosted_storage.photo_call("DELETE", key, timeout=min(remaining, 30.0))
            elif bearer_token:
                hosted_photos.storage_call("DELETE", key, bearer_token, timeout=min(remaining, 20.0))
        except Exception:
            continue


def _best_effort_delete_photo_files(
    db: Session,
    stored_key: str | None,
    source_key: str | None,
    *,
    bearer_token: str | None,
    deferred_deletes: list[str] | None = None,
) -> None:
    keys = [k for k in (stored_key, source_key) if k]
    if not keys:
        return
    if hasattr(db, "user_id"):
        if deferred_deletes is not None:
            deferred_deletes.extend(keys)
            return
        delete_hosted_storage_keys(keys, bearer_token=bearer_token)
        return
    from anteroom.services.photo_store import photo_store

    store = photo_store()
    for key in keys:
        try:
            store.delete(key)
        except Exception:
            continue


def _require_str(args: dict[str, Any], key: str, *, max_len: int = 500) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise McpToolError(f"{key} is required.")
    cleaned = val.strip()
    if len(cleaned) > max_len:
        raise McpToolError(f"{key} must be at most {max_len} characters.")
    return cleaned


def _bounded_int(val: Any, *, default: int, minimum: int, maximum: int) -> int:
    if val is None:
        return default
    if isinstance(val, bool) or not isinstance(val, int):
        raise McpToolError("Expected an integer.")
    return max(minimum, min(maximum, val))


def _parse_optional_date(val: Any, name: str) -> date | None:
    if val is None or val == "":
        return None
    if not isinstance(val, str):
        raise McpToolError(f"{name} must be an ISO date string (YYYY-MM-DD).")
    try:
        return date.fromisoformat(val.strip())
    except ValueError as exc:
        raise McpToolError(f"{name} must be an ISO date string (YYYY-MM-DD).") from exc


def _dedupe_labels(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        label = str(raw or "").strip()
        if not label:
            continue
        folded = label.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        out.append(label)
    return out
