"""API-key AI providers: keys, models, connection tests and OpenRouter sign-in."""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from vendoo_studio.services.ai_key_catalog import KEY_PROVIDERS, key_provider

router = APIRouter()
sign_in_router = APIRouter(prefix="/api/settings/ai-keys", tags=["settings"])
key_router = APIRouter(prefix="/api/settings/ai-keys", tags=["settings"])


class AIKeyConfig(BaseModel):
    api_key: str


class AIKeyModelsConfig(BaseModel):
    vision_model: str | None = None
    listing_model: str | None = None
    reasoning_effort: str | None = None


# Efforts OpenRouter accepts across providers; a model's own catalog entry is
# preferred when it advertises a narrower list.
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


def _spec(provider_id: str):
    try:
        return key_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


def _instance(provider_id: str):
    from vendoo_studio.services.keychain import get_ai_key
    from vendoo_studio.services.listing_provider import key_provider_instance

    _spec(provider_id)
    key = get_ai_key(provider_id)
    if not key:
        raise HTTPException(400, f"Add a {KEY_PROVIDERS[provider_id].label} API key first.")
    provider = key_provider_instance(provider_id, key)
    from vendoo_studio.services.service_context import current
    if current() is not None:
        from vendoo_studio.services.hosted_provider import HostedProvider
        return HostedProvider(provider)
    return provider


@key_router.get("")
def list_ai_keys():
    from vendoo_studio.services.keychain import get_ai_keys, mask_secret
    from vendoo_studio.services.user_settings import resolved_key_provider_models

    keys = get_ai_keys()
    providers = []
    for spec in KEY_PROVIDERS.values():
        vision, listing = resolved_key_provider_models(spec.id)
        providers.append({
            "id": spec.id,
            "label": spec.label,
            "key_url": spec.key_url,
            "key_hint": spec.key_hint,
            "web_search": spec.web_search,
            "sign_in": spec.sign_in,
            "masked_key": mask_secret(keys.get(spec.id)),
            "vision_model": vision,
            "listing_model": listing,
        })
    return {"providers": providers}


@key_router.put("/{provider_id}")
def set_ai_key(provider_id: str, config: AIKeyConfig):
    from vendoo_studio.services.keychain import set_ai_key as save_key

    _spec(provider_id)
    key = config.api_key.strip().strip('"').strip("'")
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if not key:
        raise HTTPException(400, "API key is required")
    save_key(provider_id, key)
    return {"ok": True}


@key_router.delete("/{provider_id}")
def delete_ai_key(provider_id: str):
    from vendoo_studio.services.keychain import delete_ai_key as remove_key

    _spec(provider_id)
    remove_key(provider_id)
    return {"ok": True}


@key_router.post("/{provider_id}/test")
async def test_ai_key(provider_id: str):
    # Reading the key opens a database session on the web; keep it off the event loop.
    provider = await run_in_threadpool(_instance, provider_id)
    try:
        await provider.test_connection()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


@key_router.get("/{provider_id}/models")
async def ai_key_models(provider_id: str):
    from vendoo_studio.services.user_settings import get_key_provider_reasoning

    spec = _spec(provider_id)
    provider = await run_in_threadpool(_instance, provider_id)
    entries: list = []
    try:
        list_entries = getattr(provider, "list_model_entries", None)
        if list_entries is not None:
            entries = await list_entries()
        else:
            from vendoo_studio.providers.chat_completions import ModelEntry

            entries = [ModelEntry(slug, slug) for slug in await provider.list_models()]
        error = None
    except Exception as exc:
        error = str(exc)
    models = sorted(entry.slug for entry in entries)
    labels = {entry.slug: entry.name for entry in entries if entry.name != entry.slug}
    reasoning = None
    if spec.reasoning and error is None:
        entry = next((item for item in entries if item.slug == provider.listing_model), None)
        if entry is not None and entry.reasoning_efforts:
            efforts = entry.reasoning_efforts
            saved = await run_in_threadpool(get_key_provider_reasoning, provider_id)
            value = "default"
            if (
                saved
                and saved["model"] == provider.listing_model
                and saved["value"] in efforts
            ):
                value = saved["value"]
            reasoning = {
                "options": [
                    {"value": "default", "label": "Default"},
                    *({"value": effort, "label": effort.capitalize()} for effort in efforts),
                ],
                "value": value,
            }
    return {
        "models": models,
        "labels": labels,
        "vision_model": provider.vision_model,
        "listing_model": provider.listing_model,
        "reasoning": reasoning,
        "error": error,
    }


@key_router.put("/{provider_id}/models")
def set_ai_key_models(provider_id: str, config: AIKeyModelsConfig):
    from vendoo_studio.services.user_settings import (
        clear_key_provider_reasoning,
        resolved_key_provider_models,
        set_key_provider_models,
        set_key_provider_reasoning,
    )

    spec = _spec(provider_id)
    reasoning = (config.reasoning_effort or "").strip().lower()
    if not (config.vision_model or config.listing_model or reasoning):
        raise HTTPException(400, "Choose a photo model, listing model, or reasoning level.")
    if reasoning and not spec.reasoning:
        raise HTTPException(400, f"{spec.label} has no reasoning setting.")
    if reasoning not in ("", "default", *REASONING_EFFORTS):
        raise HTTPException(400, f"{spec.label} does not support reasoning level {reasoning}.")
    try:
        if config.vision_model or config.listing_model:
            models = set_key_provider_models(
                provider_id,
                vision_model=config.vision_model,
                listing_model=config.listing_model,
            )
        else:
            models = {}
        if reasoning:
            _, listing_model = resolved_key_provider_models(provider_id)
            if reasoning == "default":
                clear_key_provider_reasoning(provider_id)
            else:
                set_key_provider_reasoning(provider_id, model=listing_model, value=reasoning)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **models}


@sign_in_router.post("/openrouter/sign-in")
def openrouter_sign_in():
    """Open OpenRouter's sign-in on this Mac; the page also shows the link."""
    import webbrowser

    from vendoo_studio.services.openrouter_oauth import start_sign_in

    url = start_sign_in()
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - the page shows the link too
        pass
    return {"authorize_url": url}


def _page(title: str, message: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Anteroom</title>"
        "<body style='font:15px system-ui;margin:4rem auto;max-width:28rem;text-align:center'>"
        f"<h1 style='font-size:1.25rem'>{html.escape(title)}</h1><p>{html.escape(message)}</p></body>"
    )


@sign_in_router.get("/openrouter/callback", response_class=HTMLResponse)
async def openrouter_callback(code: str = "", state: str = "", error: str = ""):
    from vendoo_studio.services.openrouter_oauth import finish_sign_in

    if error or not code:
        return _page("OpenRouter sign-in did not finish", error or "No sign-in code came back.")
    try:
        await finish_sign_in(code, state)
    except Exception as exc:
        return _page("OpenRouter sign-in did not finish", str(exc))
    return _page("OpenRouter is connected", "You can close this tab and go back to Anteroom.")


router.include_router(key_router)
router.include_router(sign_in_router)
