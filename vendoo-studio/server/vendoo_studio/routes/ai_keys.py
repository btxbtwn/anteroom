"""API-key AI providers: keys, models, connection tests and OpenRouter sign-in."""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from vendoo_studio.services.ai_key_catalog import KEY_PROVIDERS, key_provider

router = APIRouter(prefix="/api/settings/ai-keys", tags=["settings"])


class AIKeyConfig(BaseModel):
    api_key: str


class AIKeyModelsConfig(BaseModel):
    vision_model: str | None = None
    listing_model: str | None = None


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
    return key_provider_instance(provider_id, key)


@router.get("")
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


@router.put("/{provider_id}")
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


@router.delete("/{provider_id}")
def delete_ai_key(provider_id: str):
    from vendoo_studio.services.keychain import delete_ai_key as remove_key

    _spec(provider_id)
    remove_key(provider_id)
    return {"ok": True}


@router.post("/{provider_id}/test")
async def test_ai_key(provider_id: str):
    provider = _instance(provider_id)
    try:
        await provider.test_connection()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


@router.get("/{provider_id}/models")
async def ai_key_models(provider_id: str):
    provider = _instance(provider_id)
    try:
        models = await provider.list_models()
        error = None
    except Exception as exc:
        models, error = [], str(exc)
    return {
        "models": models,
        "vision_model": provider.vision_model,
        "listing_model": provider.listing_model,
        "error": error,
    }


@router.put("/{provider_id}/models")
def set_ai_key_models(provider_id: str, config: AIKeyModelsConfig):
    from vendoo_studio.services.user_settings import set_key_provider_models

    _spec(provider_id)
    try:
        models = set_key_provider_models(
            provider_id,
            vision_model=config.vision_model,
            listing_model=config.listing_model,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **models}


@router.post("/openrouter/sign-in")
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


@router.get("/openrouter/callback", response_class=HTMLResponse)
async def openrouter_callback(code: str = "", state: str = "", error: str = ""):
    from vendoo_studio.services.openrouter_oauth import finish_sign_in

    if error or not code:
        return _page("OpenRouter sign-in did not finish", error or "No sign-in code came back.")
    try:
        await finish_sign_in(code, state)
    except Exception as exc:
        return _page("OpenRouter sign-in did not finish", str(exc))
    return _page("OpenRouter is connected", "You can close this tab and go back to Anteroom.")
