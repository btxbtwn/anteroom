from __future__ import annotations

from typing import Annotated, Literal, TYPE_CHECKING, cast

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from anteroom.providers.anthropic_api import AnthropicAPIProvider
    from anteroom.providers.chat_completions import ChatCompletionsProvider
    from anteroom.providers.openai_api import OpenAIAPIProvider
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services.ai_key_catalog import KEY_PROVIDER_IDS, KEY_PROVIDERS
from anteroom.services.user_settings import SIGN_IN_PROVIDER_CHOICES

router = APIRouter(prefix="/api/settings", tags=["settings"])


_PROVIDER_IDS = (*SIGN_IN_PROVIDER_CHOICES, *KEY_PROVIDER_IDS)
ProviderChoice = Annotated[str, Field(pattern="^(?:" + "|".join(_PROVIDER_IDS) + ")$", json_schema_extra={"enum": list(_PROVIDER_IDS)})]
FallbackChoice = Annotated[str, Field(pattern="^(?:" + "|".join((*_PROVIDER_IDS, "none")) + ")$", json_schema_extra={"enum": [*_PROVIDER_IDS, "none"]})]
PhotoChoice = Annotated[str, Field(pattern="^(?:" + "|".join(("same", *_PROVIDER_IDS)) + ")$", json_schema_extra={"enum": ["same", *_PROVIDER_IDS]})]


class ChatGPTModelsConfig(BaseModel):
    vision_model: str | None = None
    listing_model: str | None = None
    reasoning_effort: str | None = None


class CursorModelsConfig(BaseModel):
    vision_model: str | None = None
    listing_model: str | None = None
    reasoning_effort: str | None = None


class ClaudeModelsConfig(BaseModel):
    vision_model: str | None = None
    listing_model: str | None = None
    effort: str | None = None


class ProviderConfig(BaseModel):
    api_key: str | None = None


class MarketplacesConfig(BaseModel):
    selected: list[str]


class ShipsFromZipConfig(BaseModel):
    zip: str = ""


class ListingFieldDefaultConfig(BaseModel):
    path: str
    value: str
    label: str | None = None


class PackageDimensionsConfig(BaseModel):
    length: int
    width: int
    height: int


class HiddenFieldConfig(BaseModel):
    marketplace: str
    field: str
    label: str | None = None
    scope: Literal["always", "listing"]
    conversation_id: str | None = None


class ChatGPTStatus(BaseModel):
    signed_in: bool
    email: str | None = None
    plan: str | None = None
    pending: dict | None = None
    error: str | None = None


class ClaudeStatus(BaseModel):
    installed: bool
    signed_in: bool
    email: str | None = None
    plan: str | None = None
    install_command: str


class ProviderStatus(BaseModel):
    provider: str
    primary: ProviderChoice
    fallback: FallbackChoice
    photo_provider: PhotoChoice = "same"
    configured: bool
    masked_key: str | None
    masked_cursor_key: str | None = None
    vision_model: str
    listing_model: str
    base_url: str
    chatgpt: ChatGPTStatus
    claude: ClaudeStatus


class PhotoProviderConfig(BaseModel):
    choice: PhotoChoice


class PreferredProviderConfig(BaseModel):
    primary: ProviderChoice
    fallback: FallbackChoice | None = None


def _chatgpt_status() -> ChatGPTStatus:
    from anteroom.services.service_context import current
    if (context := current()) is not None:
        from anteroom.services.mac_inference import existing_relay
        connection = existing_relay(context.seller_id)
        return ChatGPTStatus(signed_in=connection is not None and connection.ready('chatgpt'))
    from anteroom.services import chatgpt_oauth
    from anteroom.services.keychain import get_chatgpt_tokens

    tokens = get_chatgpt_tokens() if chatgpt_oauth.chatgpt_signed_in() else None
    profile = chatgpt_oauth.profile_from_tokens(tokens)
    return ChatGPTStatus(
        signed_in=bool(tokens),
        email=profile.get("email"),
        plan=profile.get("plan"),
        pending=chatgpt_oauth.pending_login(),
        error=chatgpt_oauth.login_error(),
    )


@router.get("/provider")
def get_provider():
    from anteroom.services.service_context import current
    if current() is not None:
        from anteroom.services.hosted_provider import managed_ai_enabled, managed_model
        if managed_ai_enabled():
            model = managed_model()
            return ProviderStatus(
                provider="openrouter",
                primary="openrouter",
                fallback="none",
                photo_provider="same",
                configured=True,
                masked_key=None,
                masked_cursor_key=None,
                vision_model=model,
                listing_model=model,
                base_url=KEY_PROVIDERS["openrouter"].base_url,
                chatgpt=ChatGPTStatus(signed_in=False),
                claude=ClaudeStatus(installed=False, signed_in=False, install_command=""),
            )
        from anteroom.routes.mac_inference import _pinned
        if pinned := _pinned('mac', claim_route=False):
            return pinned
    from anteroom.services.keychain import get_api_key, get_cursor_api_key, mask_secret
    from anteroom.services.listing_provider import get_listing_provider
    from anteroom.services.user_settings import get_listing_provider_order, get_photo_provider_choice

    from anteroom.services import claude_auth

    chatgpt = _chatgpt_status()
    if (context := current()) is not None:
        from anteroom.services.mac_inference import existing_relay
        connection = existing_relay(context.seller_id)
        claude = ClaudeStatus(installed=True, signed_in=connection is not None and connection.ready('claude'), install_command="")
    else:
        claude = ClaudeStatus(**claude_auth.status())
    photo_provider = get_photo_provider_choice()
    key = get_api_key()
    masked = mask_secret(key)
    masked_cursor = mask_secret(get_cursor_api_key())
    primary, fallback = get_listing_provider_order()
    active = get_listing_provider()
    active_name = getattr(active, "name", None)

    if active_name == "chatgpt" and active is not None:
        from anteroom.providers.chatgpt_codex import resolved_chatgpt_models

        if current() is not None:
            vision_model, listing_model = active.vision_model, active.listing_model
        else:
            vision_model, listing_model = resolved_chatgpt_models()
        return ProviderStatus(
            provider="chatgpt",
            primary=primary,
            fallback=fallback,
            photo_provider=photo_provider,
            configured=True,
            masked_key=masked,
            masked_cursor_key=masked_cursor,
            vision_model=vision_model,
            listing_model=listing_model,
            base_url="https://api.openai.com/v1",
            chatgpt=chatgpt,
            claude=claude,
        )

    if active_name == "claude" and active is not None:
        return ProviderStatus(
            provider="claude",
            primary=primary,
            fallback=fallback,
            photo_provider=photo_provider,
            configured=True,
            masked_key=masked,
            masked_cursor_key=masked_cursor,
            vision_model=active.vision_model,
            listing_model=active.listing_model,
            base_url="claude-agent-sdk://local",
            chatgpt=chatgpt,
            claude=claude,
        )

    if active_name == "cursor":
        from anteroom.services.user_settings import resolved_cursor_models

        vision_model, listing_model = resolved_cursor_models()
        return ProviderStatus(
            provider="cursor",
            primary=primary,
            fallback=fallback,
            photo_provider=photo_provider,
            configured=True,
            masked_key=masked,
            masked_cursor_key=masked_cursor,
            vision_model=vision_model,
            listing_model=listing_model,
            base_url="cursor-sdk://local",
            chatgpt=chatgpt,
            claude=claude,
        )

    if active_name in KEY_PROVIDERS and active is not None:
        return ProviderStatus(
            provider=active_name,
            primary=primary,
            fallback=fallback,
            photo_provider=photo_provider,
            configured=True,
            masked_key=masked,
            masked_cursor_key=masked_cursor,
            vision_model=active.vision_model,
            listing_model=active.listing_model,
            base_url=cast("OpenAIAPIProvider | AnthropicAPIProvider | ChatCompletionsProvider", active).base_url,
            chatgpt=chatgpt,
            claude=claude,
        )

    return ProviderStatus(
        provider="xiaomi-mimo",
        primary=primary,
        fallback=fallback,
        photo_provider=photo_provider,
        configured=bool(active),
        masked_key=masked,
        masked_cursor_key=masked_cursor,
        vision_model="mimo-v2.5",
        listing_model="mimo-v2.5-pro",
        base_url="https://api.xiaomimimo.com/v1",
        chatgpt=chatgpt,
        claude=claude,
    )


@router.put("/provider")
def set_provider(config: ProviderConfig):
    from anteroom.providers.xiaomi_mimo import normalize_mimo_api_key
    from anteroom.services.keychain import set_api_key

    key = normalize_mimo_api_key(config.api_key or "")
    if not key:
        raise HTTPException(400, "API key is required")
    set_api_key(key)
    return {"ok": True}


@router.put("/provider/preferred")
def set_preferred_provider(config: PreferredProviderConfig):
    from anteroom.services.user_settings import set_listing_provider_order

    try:
        order = set_listing_provider_order(config.primary, config.fallback)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **order}


@router.put("/provider/photos")
def set_photo_provider(config: PhotoProviderConfig):
    from anteroom.services.user_settings import set_photo_provider_choice

    return {"ok": True, "photo_provider": set_photo_provider_choice(config.choice)}


@router.delete("/provider/key")
def delete_key():
    from anteroom.services.keychain import delete_api_key

    delete_api_key()
    return {"ok": True}


@router.post("/provider/test")
async def test_connection():
    from anteroom.services.listing_provider import get_listing_provider

    provider = get_listing_provider()
    if provider is None:
        raise HTTPException(
            400,
            "Sign in with ChatGPT or Claude in Settings, or add an API key for any provider.",
        )

    name = getattr(provider, "name", "xiaomi-mimo")
    try:
        ok = await provider.test_connection()
    except Exception as exc:
        return {"ok": False, "provider": name, "error": str(exc)}
    return {"ok": ok, "provider": name, "error": None if ok else "Connection failed"}


@router.post("/provider/mimo/test")
async def test_mimo():
    """Always exercise the saved MiMo key, even when another provider is primary."""
    from anteroom.providers.xiaomi_mimo import MiMoProvider
    from anteroom.services.keychain import get_api_key

    key = get_api_key()
    if not key:
        raise HTTPException(400, "Add a MiMo API key in Settings.")
    provider = MiMoProvider(api_key=key)
    from anteroom.services.service_context import current
    if current() is not None:
        from anteroom.services.hosted_provider import HostedProvider
        provider = HostedProvider(provider)
    try:
        ok = await provider.test_connection()
    except Exception as exc:
        return {"ok": False, "provider": "xiaomi-mimo", "error": str(exc)}
    return {
        "ok": ok,
        "provider": "xiaomi-mimo",
        "error": None if ok else "Connection failed",
    }


@router.get("/chatgpt/models")
async def chatgpt_models():
    from anteroom.providers.chatgpt_codex import (
        clamp_reasoning_effort,
        fetch_chatgpt_models,
        resolved_chatgpt_models,
        resolved_chatgpt_reasoning,
        supported_reasoning_efforts,
    )
    from anteroom.services.chatgpt_oauth import chatgpt_signed_in

    if not chatgpt_signed_in():
        raise HTTPException(400, "Sign in with ChatGPT first.")

    vision_model, listing_model = resolved_chatgpt_models()
    error = None
    try:
        slugs = await fetch_chatgpt_models()
    except Exception as exc:
        slugs = []
        error = str(exc)
    for slug in (vision_model, listing_model):
        if slug and slug not in slugs:
            slugs.append(slug)
    reasoning_efforts = list(supported_reasoning_efforts(listing_model))
    reasoning_effort = clamp_reasoning_effort(resolved_chatgpt_reasoning(), listing_model)
    return {
        "models": slugs,
        "vision_model": vision_model,
        "listing_model": listing_model,
        "reasoning_effort": reasoning_effort,
        "reasoning_efforts": reasoning_efforts,
        "error": error,
    }


@router.put("/chatgpt/models")
def set_chatgpt_models(config: ChatGPTModelsConfig):
    from anteroom.providers.chatgpt_codex import resolved_chatgpt_models
    from anteroom.services.chatgpt_oauth import chatgpt_signed_in
    from anteroom.services.keychain import set_chatgpt_models as persist_chatgpt_models

    if not chatgpt_signed_in():
        raise HTTPException(400, "Sign in with ChatGPT first.")

    vision = (config.vision_model or "").strip()
    listing = (config.listing_model or "").strip()
    reasoning = (config.reasoning_effort or "").strip()
    if not vision and not listing and not reasoning:
        raise HTTPException(400, "Choose a vision model, listing model, or reasoning mode.")
    persist_chatgpt_models(
        vision_model=vision or None,
        listing_model=listing or None,
        reasoning_effort=reasoning or None,
    )
    vision_model, listing_model = resolved_chatgpt_models()
    from anteroom.providers.chatgpt_codex import clamp_reasoning_effort, resolved_chatgpt_reasoning

    return {
        "ok": True,
        "vision_model": vision_model,
        "listing_model": listing_model,
        "reasoning_effort": clamp_reasoning_effort(resolved_chatgpt_reasoning(), listing_model),
    }


def _cursor_reasoning_payload(catalog: list, listing_model: str) -> dict | None:
    """The listing model's reasoning choices and the level in effect, if it has any."""
    from anteroom.providers.cursor_agent import default_param_value, reasoning_parameter
    from anteroom.services.user_settings import get_cursor_reasoning

    model = next((item for item in catalog if item.id == listing_model), None)
    param = reasoning_parameter(model) if model is not None else None
    if param is None:
        return None
    options = [{"value": item.value, "label": item.display_name or item.value} for item in param.values]
    values = [item["value"] for item in options]
    saved = get_cursor_reasoning()
    value = saved["value"] if saved and saved["model"] == listing_model and saved["param"] == param.id else None
    if value not in values:
        value = default_param_value(model, param.id)
    return {
        "param": param.id,
        "options": options,
        "value": value if value in values else values[0],
    }


async def _cursor_catalog(key: str) -> list:
    import asyncio

    from anteroom.providers.cursor_agent import CursorProvider

    return await asyncio.to_thread(CursorProvider(api_key=key).list_models)


@router.get("/cursor/models")
async def cursor_models():
    """Return saved Cursor model prefs and catalog IDs when a key is present."""
    from anteroom.services.keychain import get_cursor_api_key
    from anteroom.services.user_settings import (
        AUTO_CURSOR_MODEL,
        DEFAULT_CURSOR_MODEL,
        resolved_cursor_models,
    )

    vision_model, listing_model = resolved_cursor_models()
    slugs = [AUTO_CURSOR_MODEL, DEFAULT_CURSOR_MODEL]
    catalog: list = []
    error = None
    key = get_cursor_api_key()
    if key:
        try:
            catalog = await _cursor_catalog(key)
        except Exception as exc:
            error = str(exc)
    for slug in [item.id for item in catalog] + [vision_model, listing_model]:
        if slug and slug not in slugs:
            slugs.append(slug)
    # Cursor's own names for its models ("Grok 4.7", "Claude Opus 5.5"), for the picker.
    labels = {AUTO_CURSOR_MODEL: "Auto"}
    for item in catalog:
        name = getattr(item, "display_name", None)
        if item.id and isinstance(name, str) and name.strip():
            labels[item.id] = name.strip()
    return {
        "models": slugs,
        "labels": labels,
        "vision_model": vision_model,
        "listing_model": listing_model,
        "reasoning": _cursor_reasoning_payload(catalog, listing_model),
        "error": error,
    }


@router.put("/cursor/models")
async def set_cursor_models(config: CursorModelsConfig):
    from anteroom.services.keychain import get_cursor_api_key
    from anteroom.services.user_settings import (
        resolved_cursor_models,
        set_cursor_models as persist_cursor_models,
        set_cursor_reasoning,
    )

    key = get_cursor_api_key()
    if not key:
        raise HTTPException(400, "Add a Cursor API key in Settings.")

    vision = (config.vision_model or "").strip()
    listing = (config.listing_model or "").strip()
    reasoning = (config.reasoning_effort or "").strip()
    if not vision and not listing and not reasoning:
        raise HTTPException(400, "Choose a vision model, listing model, or reasoning level.")
    if vision or listing:
        try:
            persist_cursor_models(vision_model=vision or None, listing_model=listing or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    vision_model, listing_model = resolved_cursor_models()
    if reasoning:
        try:
            catalog = await _cursor_catalog(key)
        except Exception as exc:
            raise HTTPException(502, f"Could not load Cursor models: {exc}") from exc
        choices = _cursor_reasoning_payload(catalog, listing_model)
        if choices is None:
            raise HTTPException(400, f"{listing_model} has no reasoning setting.")
        if reasoning not in [item["value"] for item in choices["options"]]:
            raise HTTPException(400, f"{listing_model} does not support reasoning level {reasoning}.")
        set_cursor_reasoning(model=listing_model, param=choices["param"], value=reasoning)
    return {"ok": True, "vision_model": vision_model, "listing_model": listing_model}


@router.get("/usage-limits")
async def usage_limits(refresh: bool = False):
    """Each provider's subscription usage, as percentages and reset times."""
    from anteroom.services.service_context import current
    if current() is not None:
        from anteroom.services.hosted_provider import managed_ai_enabled
        if managed_ai_enabled():
            return {"ok": True, "providers": []}
    from anteroom.services.usage_limits import all_usage_limits

    return {"ok": True, "providers": await all_usage_limits(force=refresh)}


@router.get("/claude/models")
async def claude_models():
    """The account's Claude models, the saved picks, and the listing model's effort levels."""
    from anteroom.providers.claude_agent import list_models
    from anteroom.services.claude_auth import claude_signed_in
    from anteroom.services.user_settings import get_claude_models, resolved_claude_models

    if not claude_signed_in():
        raise HTTPException(400, "Sign in with Claude first.")
    vision_model, listing_model = resolved_claude_models()
    error = None
    try:
        models = await list_models()
    except Exception as exc:
        models = []
        error = str(exc)
    for slug in (vision_model, listing_model):
        if not any(item["value"] == slug for item in models):
            models.append({"value": slug, "label": slug, "efforts": []})
    efforts = next(item["efforts"] for item in models if item["value"] == listing_model)
    effort = get_claude_models().get("effort")
    return {
        "models": models,
        "vision_model": vision_model,
        "listing_model": listing_model,
        "effort": effort if effort in efforts else None,
        "efforts": efforts,
        "error": error,
    }


@router.put("/claude/models")
def set_claude_models(config: ClaudeModelsConfig):
    from anteroom.services.claude_auth import claude_signed_in
    from anteroom.services.user_settings import set_claude_models as persist_claude_models

    if not claude_signed_in():
        raise HTTPException(400, "Sign in with Claude first.")
    try:
        saved = persist_claude_models(
            vision_model=config.vision_model,
            listing_model=config.listing_model,
            effort=config.effort,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **saved}


def _marketplaces_payload(selected: list[str]) -> dict:
    from anteroom.services.marketplaces import catalog_payload, selected_fillable_platforms

    return {
        "available": catalog_payload(),
        "selected": selected,
        "fillable": selected_fillable_platforms(selected),
    }


class SellerRegionConfig(BaseModel):
    country: Literal["US", "CA", "GB", "AU"]


@router.get("/seller-region")
def get_seller_region():
    from anteroom.services.seller_region import seller_region

    return seller_region()


@router.put("/seller-region")
def put_seller_region(config: SellerRegionConfig):
    from anteroom.services.seller_region import set_seller_region

    return set_seller_region(config.country)


@router.get("/marketplaces")
def get_marketplaces():
    from anteroom.services.marketplaces import get_selected_marketplaces

    return _marketplaces_payload(get_selected_marketplaces())


@router.put("/marketplaces")
def set_marketplaces(config: MarketplacesConfig):
    from anteroom.services.marketplaces import set_selected_marketplaces

    try:
        selected = set_selected_marketplaces(config.selected)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **_marketplaces_payload(selected)}


@router.get("/package-dimensions")
def get_package_dimensions():
    from anteroom.services.user_settings import get_package_dimensions as load_package_dimensions

    return load_package_dimensions()


@router.put("/package-dimensions")
def set_package_dimensions(config: PackageDimensionsConfig):
    from anteroom.services.user_settings import set_package_dimensions as save_package_dimensions

    try:
        dimensions = save_package_dimensions(
            length=config.length,
            width=config.width,
            height=config.height,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **dimensions}


@router.get("/ships-from-zip")
def get_ships_from_zip():
    from anteroom.services.user_settings import get_ships_from_zip as load_zip

    return {"zip": load_zip()}


@router.put("/ships-from-zip")
def set_ships_from_zip(config: ShipsFromZipConfig):
    from anteroom.services.user_settings import set_ships_from_zip as save_zip

    try:
        return {"ok": True, "zip": save_zip(config.zip)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/listing-defaults")
def get_listing_defaults():
    from anteroom.services.user_settings import get_listing_field_defaults

    return {"defaults": get_listing_field_defaults()}


@router.put("/listing-defaults")
def set_listing_default(config: ListingFieldDefaultConfig):
    from anteroom.services.user_settings import set_listing_field_default as save_default

    try:
        return {"ok": True, "defaults": save_default(config.path, config.value, config.label)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/listing-defaults")
def clear_listing_default(path: str):
    from anteroom.services.user_settings import clear_listing_field_default

    try:
        return {"ok": True, "defaults": clear_listing_field_default(path)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/ebay-inventory-location")
def get_ebay_inventory_location():
    from anteroom.services.user_settings import get_ebay_inventory_location as load_location

    return {"location": load_location()}


@router.get("/hidden-fields")
def get_hidden_fields(conversation_id: str | None = None):
    from anteroom.services.hidden_fields import hidden_fields

    return hidden_fields(conversation_id)


@router.put("/hidden-fields")
def hide_hidden_field(config: HiddenFieldConfig):
    from anteroom.services.hidden_fields import hide_field

    try:
        return {
            "ok": True,
            **hide_field(
                marketplace=config.marketplace,
                field=config.field,
                scope=config.scope,
                conversation_id=config.conversation_id,
                label=config.label,
            ),
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/hidden-fields")
def restore_hidden_field(config: HiddenFieldConfig):
    from anteroom.services.hidden_fields import restore_field

    try:
        return {
            "ok": True,
            **restore_field(
                marketplace=config.marketplace,
                field=config.field,
                scope=config.scope,
                conversation_id=config.conversation_id,
            ),
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class RestoreAllHiddenFields(BaseModel):
    conversation_id: str | None = None


class RestoreMatchingHiddenFields(BaseModel):
    conversation_id: str | None = None
    fields: list[dict[str, str]]


@router.post("/hidden-fields/restore-all")
def restore_all_hidden_fields(body: RestoreAllHiddenFields):
    from anteroom.services.hidden_fields import restore_all_fields

    return {"ok": True, **restore_all_fields(body.conversation_id)}


@router.post("/hidden-fields/restore-matching")
def restore_matching_hidden_fields(body: RestoreMatchingHiddenFields):
    from anteroom.services.hidden_fields import restore_matching_fields

    matches = [
        (str(item.get("marketplace") or ""), str(item.get("field") or ""))
        for item in body.fields
        if isinstance(item, dict)
    ]
    return {
        "ok": True,
        **restore_matching_fields(matches, body.conversation_id),
    }


@router.post("/chatgpt/login")
async def chatgpt_login():
    from anteroom.services import chatgpt_oauth

    try:
        pending = await chatgpt_oauth.start_login()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return pending


@router.delete("/chatgpt/login")
async def chatgpt_login_cancel():
    from anteroom.services import chatgpt_oauth

    await chatgpt_oauth.cancel_login()
    return {"ok": True}


@router.delete("/chatgpt")
async def chatgpt_logout():
    from anteroom.services import chatgpt_oauth

    await chatgpt_oauth.cancel_login()
    revoked = await chatgpt_oauth.logout()
    return {"ok": True, "revoked": revoked}


@router.websocket("/claude/terminal")
async def claude_login_terminal(ws: WebSocket):
    """Run `claude auth login` in Settings' sign-in terminal.

    Output goes to the page as binary frames; the page sends
    ``{"type": "input", "data"}`` and ``{"type": "resize", "cols", "rows"}``.
    The last frame is ``{"type": "exit", "code"}``, or ``{"type": "error"}``.
    """
    from anteroom.services.claude_auth import LoginTerminal

    # Only Studio's own window on this Mac: the login opens a browser here.
    from anteroom.services.local_requests import origin_allowed

    if not origin_allowed(ws.headers.get("origin") or ""):
        await ws.close(code=1008)
        return
    await ws.accept()
    cols = int(ws.query_params.get("cols") or 80)
    rows = int(ws.query_params.get("rows") or 24)
    try:
        terminal = LoginTerminal(cols, rows)
    except Exception as exc:
        await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
        await ws.close()
        return

    async def pump_output() -> None:
        while (data := await terminal.read()) is not None:
            await ws.send_bytes(data)

    async def pump_input() -> None:
        while True:
            try:
                message = json.loads(await ws.receive_text())
            except WebSocketDisconnect:
                return
            if message.get("type") == "input":
                terminal.write(str(message.get("data") or "").encode())
            elif message.get("type") == "resize":
                terminal.resize(int(message.get("cols") or cols), int(message.get("rows") or rows))

    output = asyncio.create_task(pump_output())
    pump = asyncio.create_task(pump_input())
    try:
        done, _ = await asyncio.wait({output, pump}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        pump.cancel()
        output.cancel()
        code = await terminal.close()
    # The page is still there when the command, not the page, finished first.
    if output in done and output.exception() is None:
        try:
            await ws.send_text(json.dumps({"type": "exit", "code": code}))
            await ws.close()
        except (WebSocketDisconnect, RuntimeError):
            pass


class BraveConfig(BaseModel):
    api_key: str | None = None


class CursorConfig(BaseModel):
    api_key: str | None = None


def _brave_payload() -> dict:
    from anteroom.services.keychain import get_brave_api_key, mask_secret

    key = get_brave_api_key()
    return {"configured": bool(key), "masked_key": mask_secret(key)}


def _cursor_payload() -> dict:
    from anteroom.services.keychain import get_cursor_api_key, mask_secret

    key = get_cursor_api_key()
    return {"configured": bool(key), "masked_key": mask_secret(key)}


@router.get("/brave")
def get_brave():
    return _brave_payload()


@router.put("/brave")
def set_brave(config: BraveConfig):
    from anteroom.services.keychain import set_brave_api_key

    key = (config.api_key or "").strip()
    if not key:
        raise HTTPException(400, "API key is required")
    from anteroom.services.brave_search import reset_quota_backoff

    set_brave_api_key(key)
    reset_quota_backoff()
    return {"ok": True, **_brave_payload()}


@router.delete("/brave")
def delete_brave():
    from anteroom.services.keychain import delete_brave_api_key

    delete_brave_api_key()
    return {"ok": True, **_brave_payload()}


@router.post("/brave/test")
async def test_brave():
    from anteroom.services.brave_search import test_brave_connection

    ok, error = await test_brave_connection()
    return {"ok": ok, "error": error}


class PhotoroomConfig(BaseModel):
    # Checked in the handler: a validation error would echo the rejected key.
    api_key: str


def _photoroom_payload() -> dict:
    """Whether AI Studio can run, and whose Photoroom key it uses; never the key itself."""
    from anteroom import sandbox
    from anteroom.services.keychain import mask_secret, read_slot
    from anteroom.services.photoroom import KEY_ACCOUNT, configured_key

    own = read_slot(KEY_ACCOUNT)
    _key, source = configured_key()
    if source is None and sandbox.enabled():
        source = "sandbox"
    return {"configured": bool(own), "masked_key": mask_secret(own), "source": source, "available": source is not None}


@router.get("/photoroom")
def get_photoroom():
    return _photoroom_payload()


@router.put("/photoroom")
def set_photoroom(config: PhotoroomConfig):
    from anteroom.services.keychain import write_slot
    from anteroom.services.photoroom import KEY_ACCOUNT

    key = config.api_key.strip()
    if not key or len(key) > 500 or any(ch.isspace() for ch in key):
        raise HTTPException(400, "Paste the Photoroom API key without spaces.")
    if not write_slot(KEY_ACCOUNT, key):
        raise HTTPException(503, "The key could not be saved. Try again.")
    return {"ok": True, **_photoroom_payload()}


@router.delete("/photoroom")
def delete_photoroom():
    from anteroom.services.keychain import delete_slot
    from anteroom.services.photoroom import KEY_ACCOUNT

    if not delete_slot(KEY_ACCOUNT):
        raise HTTPException(503, "The key could not be removed. Try again.")
    return {"ok": True, **_photoroom_payload()}


@router.get("/cursor")
def get_cursor():
    return _cursor_payload()


@router.put("/cursor")
def set_cursor(config: CursorConfig):
    from anteroom.providers.cursor_agent import normalize_cursor_api_key
    from anteroom.services.keychain import set_cursor_api_key

    key = normalize_cursor_api_key(config.api_key or "")
    if not key:
        raise HTTPException(400, "API key is required")
    set_cursor_api_key(key)
    return {"ok": True, **_cursor_payload()}


@router.delete("/cursor")
def delete_cursor():
    from anteroom.services.keychain import delete_cursor_api_key

    delete_cursor_api_key()
    return {"ok": True, **_cursor_payload()}


@router.post("/cursor/test")
async def test_cursor():
    from anteroom.providers.cursor_agent import CursorProvider
    from anteroom.services.keychain import get_cursor_api_key

    key = get_cursor_api_key()
    if not key:
        raise HTTPException(400, "Add a Cursor API key in Settings.")
    provider = CursorProvider(api_key=key)
    try:
        ok = await provider.test_connection()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": ok, "error": None if ok else "Connection failed"}


@router.post("/setup-guide/dismiss")
def dismiss_setup_guide():
    from anteroom.services.user_settings import dismiss_setup_guide as persist_setup_guide_dismissed

    return persist_setup_guide_dismissed()


@router.get("/data-folder")
def get_data_folder():
    from anteroom.config import user_data_root

    root = user_data_root()
    return {
        "path": str(root),
        "contains": [
            "anteroom.db",
            "photos/",
            "fill-logs/",
            "settings.json",
            "pairing_token.txt",
            "catalog-index/",
            "anteroom-extension/",
            "logs/",
        ],
        "secrets": "macOS Keychain (API keys and ChatGPT tokens are not stored in this folder)",
    }


@router.get("/database")
def get_database_report(db: Session = Depends(get_db)):
    from anteroom.services.maintenance import maintenance_report

    return maintenance_report(db)


@router.post("/database/prune")
def prune_database(db: Session = Depends(get_db)):
    """Drop job events nothing reads. Space returns to disk on the next quit."""
    from anteroom.services.maintenance import maintenance_report, prune_event_bloat

    pruned = prune_event_bloat(db)
    return {"ok": True, "pruned": pruned, **maintenance_report(db)}


class UiPrefsConfig(BaseModel):
    recent_vendoo_labels: list[str] | None = None
    settled_shelf_expanded: bool | None = None
    theme: str | None = None
    listing_names: str | None = None
    remember_labels: str | list[str] | None = None
    restore_labels: list[str] | None = None
    forget_label: str | None = None
    model_favorites: list[dict] | None = None
    stale_listing_days: int | None = None
    label_colors: dict[str, str] | None = None
    listing_mode: Literal["direct", "vendoo"] | None = None


@router.get("/ui")
def get_ui_prefs():
    from anteroom.services.user_settings import get_ui_prefs as read_ui_prefs

    return {"ok": True, **read_ui_prefs()}


@router.put("/ui")
def put_ui_prefs(config: UiPrefsConfig, db: Session = Depends(get_db)):
    from anteroom.services import user_settings

    try:
        if config.listing_mode is not None:
            from anteroom.services.listing_mode import change
            change(db, config.listing_mode)
        if config.remember_labels is not None or config.restore_labels:
            user_settings.remember_vendoo_labels(config.remember_labels, config.restore_labels)
        if config.forget_label is not None:
            user_settings.forget_vendoo_label(config.forget_label)
        prefs = user_settings.set_ui_prefs(
            recent_vendoo_labels=config.recent_vendoo_labels,
            settled_shelf_expanded=config.settled_shelf_expanded,
            theme=config.theme,
            listing_names=config.listing_names,
            model_favorites=config.model_favorites,
            stale_listing_days=config.stale_listing_days,
            label_colors=config.label_colors,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **prefs}


class ListingFormulasConfig(BaseModel):
    title: str | None = None
    description: str | None = None
    blocks: list[str] | None = None


def _listing_formulas_payload() -> dict:
    from anteroom.services.skill_formulas import (
        DEFAULT_DESCRIPTION_FORMULA,
        DEFAULT_TITLE_FORMULA,
        DESCRIPTION_BLOCKS,
        NO_SECTION_DESCRIPTION,
        default_description_block_ids,
        description_block_selection,
    )
    from anteroom.services.user_settings import get_listing_formulas

    custom = get_listing_formulas()
    enabled, custom_description = description_block_selection(custom.get("description", ""))
    chosen = set(enabled)
    by_id = {block.id: block for block in DESCRIPTION_BLOCKS}
    ordered_blocks = [
        *(by_id[block_id] for block_id in enabled if block_id in by_id),
        *(block for block in DESCRIPTION_BLOCKS if block.id not in chosen),
    ]
    return {
        "title": custom.get("title", ""),
        "description": custom.get("description", ""),
        "default_title": DEFAULT_TITLE_FORMULA,
        "default_description": DEFAULT_DESCRIPTION_FORMULA,
        "default_blocks": list(default_description_block_ids()),
        "empty_description": NO_SECTION_DESCRIPTION,
        "custom_description": custom_description,
        "blocks": [
            {
                "id": block.id,
                "label": block.label,
                "detail": block.detail,
                "snippet": block.snippet,
                "default": block.default,
                "enabled": block.id in chosen,
            }
            for block in ordered_blocks
        ],
    }


@router.get("/formulas")
def get_listing_formulas():
    return {"ok": True, **_listing_formulas_payload()}


@router.put("/formulas")
def put_listing_formulas(config: ListingFormulasConfig):
    from anteroom.services.skill_formulas import DEFAULT_DESCRIPTION_FORMULA, compile_description_blocks
    from anteroom.services.user_settings import set_listing_formulas

    description = config.description
    try:
        if config.blocks is not None:
            compiled = compile_description_blocks(config.blocks)
            description = "" if compiled == DEFAULT_DESCRIPTION_FORMULA else compiled
        set_listing_formulas(title=config.title, description=description)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **_listing_formulas_payload()}


class AgentClientSnippet(BaseModel):
    id: str
    label: str
    where: str
    snippet: str
    prompt: str
    command: str | None = None


class AgentConnectionStatus(BaseModel):
    hosted: bool = False
    installed: bool
    on_path: bool
    path: str | None
    version: str | None
    version_error: str | None = None
    base_url: str
    remote_base_url: str | None
    mcp_url: str | None = None
    login_command: str
    remote_login_command: str | None
    install_command: str | None
    hint: str | None
    clients: list[AgentClientSnippet]
    remote_clients: list[AgentClientSnippet] = Field(default_factory=list)


class AgentConnectionTest(BaseModel):
    ok: bool
    detail: str
    version: str | None = None
    base_url: str | None = None


class McpTokenCreateConfig(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


@router.get("/agent", response_model=AgentConnectionStatus)
def get_agent_connection():
    from anteroom.services.agent_connection import describe

    return describe()


@router.post("/agent/test", response_model=AgentConnectionTest)
def test_agent_connection():
    """Ask the anteroom command to call this app's health route. Nothing is published."""
    from anteroom.services.agent_connection import test_connection

    return test_connection()


@router.get("/mcp-tokens")
def list_mcp_tokens(db: Session = Depends(get_db, scope="function")):
    from anteroom.services import mcp_tokens

    return {"tokens": mcp_tokens.list_tokens(db)}


@router.post("/mcp-tokens", status_code=201)
def create_mcp_token(body: McpTokenCreateConfig, db: Session = Depends(get_db, scope="function")):
    from anteroom.services import mcp_tokens

    try:
        return mcp_tokens.create_token(db, name=body.name, expires_in_days=body.expires_in_days)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/mcp-tokens/{token_id}")
def revoke_mcp_token(token_id: str, db: Session = Depends(get_db, scope="function")):
    from anteroom.services import mcp_tokens

    revoked = mcp_tokens.revoke_token(db, token_id)
    if not revoked:
        raise HTTPException(404, "Access token not found")
    return {"ok": True}


@router.get("/tailscale")
def get_tailscale():
    from anteroom.services.tailscale_serve import status as tailscale_status

    return tailscale_status()


@router.post("/tailscale/enable")
def enable_tailscale():
    from anteroom.services.tailscale_serve import TailscaleServeError, enable

    try:
        return enable()
    except TailscaleServeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/tailscale/disable")
def disable_tailscale():
    from anteroom.services.tailscale_serve import TailscaleServeError, disable

    try:
        return disable()
    except TailscaleServeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/error-logs")
def get_error_logs():
    from anteroom.services.error_log import list_entries

    return {"entries": list_entries()}


@router.delete("/error-logs")
def delete_error_logs():
    from anteroom.services.error_log import clear

    clear()
    return {"ok": True}


@router.get("/app-logs")
def get_app_logs(
    level: str = "info",
    since: str = "",
    grep: str = "",
    limit: int = 200,
):
    """Server and extension log lines kept on this Mac. Secrets are redacted."""
    from anteroom.services.error_log import query_entries

    return {"entries": query_entries(level=level, since=since or None, grep=grep or None, limit=limit)}


class SaleDetectionConfig(BaseModel):
    marketplace: str | None = None
    enabled: bool | None = None
    autodelist: bool | None = None
    auto_adjust_quantity: bool | None = None


def _sale_detection_status() -> dict:
    from anteroom.services.chrome_sales import check_errors
    from anteroom.services.user_settings import auto_adjust_quantity_enabled, autodelist_enabled, get_sale_detection

    return {
        "ok": True,
        "enabled": get_sale_detection(),
        "autodelist": autodelist_enabled(),
        "auto_adjust_quantity": auto_adjust_quantity_enabled(),
        "checks": check_errors(),
    }


@router.get("/sale-detection")
def get_sale_detection_route():
    return _sale_detection_status()


@router.put("/sale-detection")
def put_sale_detection(body: SaleDetectionConfig):
    from anteroom.services.chrome_sales import clear_check_error
    from anteroom.services.user_settings import set_auto_adjust_quantity, set_autodelist, set_sale_detection

    if (
        body.autodelist is None
        and body.auto_adjust_quantity is None
        and (body.marketplace is None or body.enabled is None)
    ):
        raise HTTPException(
            400,
            "Name the marketplace, or choose whether to delist or adjust quantities on other marketplaces.",
        )
    if body.autodelist is not None:
        set_autodelist(body.autodelist)
    if body.auto_adjust_quantity is not None:
        set_auto_adjust_quantity(body.auto_adjust_quantity)
    if body.marketplace is None and body.enabled is None:
        return _sale_detection_status()
    if body.marketplace is None or body.enabled is None:
        raise HTTPException(400, "Name the marketplace.")
    try:
        set_sale_detection(body.marketplace, body.enabled)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not body.enabled:
        clear_check_error(body.marketplace)
    return _sale_detection_status()


class PricingRuleConfig(BaseModel):
    percent: float = Field(ge=-90, le=500)
    fixed: float = Field(ge=-1000, le=1000)
    rounding: Literal["none", "99", "whole"] = "none"


class PricingRulesConfig(BaseModel):
    enabled: bool | None = None
    anchor: Literal["general", "ebay", "poshmark", "mercari", "depop", "etsy"] | None = None
    rules: dict[Literal["ebay", "poshmark", "mercari", "depop", "etsy"], PricingRuleConfig] | None = None
    reset: bool = False
    """Put every marketplace rule back on the typical-fee defaults for the anchor."""


@router.get("/pricing-rules")
def get_pricing_rules():
    from anteroom.services.pricing_rules import settings_payload

    return {"ok": True, **settings_payload()}


@router.put("/pricing-rules")
def put_pricing_rules(body: PricingRulesConfig):
    from anteroom.services import pricing_rules

    try:
        rules = {market: rule.model_dump() for market, rule in body.rules.items()} if body.rules is not None else None
        if body.reset:
            rules = pricing_rules.default_rules(body.anchor or pricing_rules.get_pricing_rules()["anchor"])
        saved = pricing_rules.set_pricing_rules(enabled=body.enabled, anchor=body.anchor, rules=rules)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **pricing_rules.settings_payload(saved)}


class ListingTransform(BaseModel):
    listing: dict
    marketplaces: list[Literal["ebay", "poshmark", "mercari", "depop", "etsy"]] | None = None


@router.post("/pricing-rules/apply")
def apply_pricing_rules(body: ListingTransform):
    """The listing with its marketplace prices back on the rules. Saves nothing and sends nothing:
    the editor saves the result like any other edit."""
    from anteroom.services import pricing_rules

    rules = pricing_rules.active_rules()
    if rules is None:
        raise HTTPException(400, "Turn on pricing rules in Settings → Listings first.")
    listing, changed = pricing_rules.apply_rules_to_listing(body.listing, rules, body.marketplaces)
    return {"listing": listing, "changed": changed}


class ShippingPresetConfig(BaseModel):
    name: str = Field(max_length=200)
    usage_condition: str = Field(default="", max_length=1000)
    length: int | None = None
    width: int | None = None
    height: int | None = None
    weight_lb: int | None = None
    weight_oz: int | None = None
    marketplaces: dict[str, dict[str, str | int | None]] = Field(default_factory=dict)


@router.get("/shipping-presets")
def get_shipping_presets():
    from anteroom.services import shipping_presets

    return {"ok": True, **shipping_presets.payload()}


def _save_preset(body: ShippingPresetConfig, preset_id: str | None) -> dict:
    from anteroom.services import shipping_presets

    try:
        shipping_presets.save_shipping_preset(body.model_dump(), preset_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **shipping_presets.payload()}


@router.post("/shipping-presets")
def create_shipping_preset(body: ShippingPresetConfig):
    return _save_preset(body, None)


@router.put("/shipping-presets/{preset_id}")
def update_shipping_preset(preset_id: str, body: ShippingPresetConfig):
    return _save_preset(body, preset_id)


@router.delete("/shipping-presets/{preset_id}")
def delete_shipping_preset(preset_id: str):
    from anteroom.services import shipping_presets

    try:
        shipping_presets.delete_shipping_preset(preset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, **shipping_presets.payload()}


@router.post("/shipping-presets/{preset_id}/apply")
def apply_shipping_preset(preset_id: str, body: ListingTransform):
    """The listing with the preset's package and shipping fields. Saves nothing and sends nothing."""
    from anteroom.services import shipping_presets

    preset = shipping_presets.find_preset(preset_id)
    if preset is None:
        raise HTTPException(404, "That shipping preset could not be found.")
    listing, paths = shipping_presets.applied_listing(body.listing, preset)
    return {"listing": listing, "paths": paths}


class ListingWarningsConfig(BaseModel):
    missing_sku: bool | None = None


@router.get("/listing-warnings")
def get_listing_warnings_route():
    from anteroom.services.user_settings import get_listing_warnings

    return {"ok": True, **get_listing_warnings()}


@router.put("/listing-warnings")
def put_listing_warnings(body: ListingWarningsConfig):
    from anteroom.services.user_settings import set_listing_warnings

    try:
        return {"ok": True, **set_listing_warnings(body.model_dump(exclude_none=True))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
