from __future__ import annotations

import json
import logging
import sys
import threading

log = logging.getLogger("vendoo_studio.keychain")

KEYRING_SERVICE = "vendoo-studio"
# Every Studio secret lives in this one Keychain item. When a build's code
# signature changes (ad-hoc builds, or a new signing certificate), every item
# it touches asks for the login password again. One item means one prompt.
SECRETS_ACCOUNT = "studio-secrets"
# Pre-consolidation items. Read once to migrate, never written or deleted so
# the migration itself doesn't add prompts.
KEYRING_ACCOUNT = "xiaomi-mimo-api-key"
BRAVE_ACCOUNT = "brave-search-api-key"
CURSOR_ACCOUNT = "cursor-api-key"
# The Sign in with ChatGPT connection. The name predates it: the old Codex
# sign-in used the same item, and chatgpt_oauth drops a record of that shape.
CHATGPT_ACCOUNT = "chatgpt-codex-oauth"
# The app registration OpenAI issued at the first sign-in, kept across sign-outs:
# {"client_id": "oaiapp_...", "redirect_uri", "subject", "email", "host_id", "sharing_enabled"}.
CHATGPT_REGISTRATION_ACCOUNT = "chatgpt-registration"
CHATGPT_MODELS_ACCOUNT = "chatgpt-models"
# This Mac's registration with Anteroom's relay: {"deviceId", "token"}.
RELAY_DEVICE_ACCOUNT = "relay-device"
# Direct marketplace sign-ins, by marketplace: {"ebay": {accountId, label,
# accessToken, refreshToken, expiresAt, refreshExpiresAt}, "etsy": {...}}.
MARKETPLACE_ACCOUNTS_ACCOUNT = "marketplace-accounts"
# The seller's own AI API keys, by provider id from ai_key_catalog:
# {"openai": "sk-...", "anthropic": "sk-ant-...", ...}.
AI_KEYS_ACCOUNT = "ai-api-keys"
LEGACY_ACCOUNTS = (KEYRING_ACCOUNT, BRAVE_ACCOUNT, CHATGPT_ACCOUNT, CHATGPT_MODELS_ACCOUNT)
# Set once the legacy sweep has run. Without it a consolidated item written
# while the Keychain was unreadable — carrying only what was set at that
# moment — would hide the secrets still in the old items for good. With it the
# sweep happens once, so steady-state launches still read a single item.
MIGRATION_MARKER = "legacy-migrated"
# Keep a marker in the data dir as well: if Keychain access becomes unavailable,
# importing the old items again could restore credentials from before rotation.
MIGRATION_FLAG_FILE = "keychain-migrated"

_lock = threading.RLock()
# Accounts with changes not yet saved. Only these override what is on disk,
# so a stale cache cannot roll back a secret another process just wrote.
_touched: set[str] = set()
_deleted: set[str] = set()
_store: dict[str, str] | None = None
_store_readable = False


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) > 12:
        return value[:8] + "..." + value[-4:]
    return "***"


def _keyring():
    """The keyring module, with a backend chosen rather than discovered.

    Packaged, this app has no usable backend: keyring finds one by reading
    entry points from installed distributions, and a frozen bundle does not
    expose them — the packaging step even strips *.dist-info, because codesign
    treats those directories as bundles. keyring then falls back to its "fail"
    backend and every secret reads as absent, which looks exactly like the
    seller never entered one. Name the macOS backend instead; there is only
    ever one right answer here.
    """
    import keyring

    try:
        from keyring.backends import fail, macOS

        if isinstance(keyring.get_keyring(), fail.Keyring):
            keyring.set_keyring(macOS.Keyring())
    except Exception:  # noqa: BLE001 - a keyring we cannot inspect is used as-is
        pass
    return keyring


def _read_password(account: str) -> str | None:
    try:
        return _keyring().get_password(KEYRING_SERVICE, account)
    except Exception:
        log.warning("keychain read failed for %s", account, exc_info=True)
        return None


def _write_password(account: str, value: str) -> bool:
    try:
        keyring = _keyring()
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            if isinstance(keyring.get_keyring(), Keyring):
                from vendoo_studio.services.macos_keychain import set_password

                set_password(KEYRING_SERVICE, account, value)
                return True
        keyring.set_password(KEYRING_SERVICE, account, value)
        return True
    except Exception as exc:
        log.warning("keychain write failed for %s (%s)", account, type(exc).__name__)
        return False


class _Unavailable(Exception):
    """Keychain refused or failed the read (e.g. Deny), as opposed to not found."""


def _read_item(account: str) -> str | None:
    try:
        return _keyring().get_password(KEYRING_SERVICE, account)
    except Exception as exc:
        log.warning("keychain read failed for %s", account, exc_info=True)
        raise _Unavailable(account) from exc


def _parse_store(raw: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        log.warning("keychain secrets item is not a JSON object; starting empty")
        return {}
    return {k: v for k, v in parsed.items() if isinstance(k, str) and isinstance(v, str)}


def _migration_flag_path():
    from pathlib import Path

    from vendoo_studio.config import DATA_DIR

    return Path(DATA_DIR) / MIGRATION_FLAG_FILE


def _migration_done_on_disk() -> bool:
    """Whether the legacy items have already been folded in, once and for all."""
    try:
        return _migration_flag_path().is_file()
    except Exception:  # noqa: BLE001 - an unreadable data dir is not a migration
        return False


def _record_migration_done() -> None:
    try:
        path = _migration_flag_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "The pre-consolidation Keychain items have been read into "
            "studio-secrets. They are a snapshot from before that and must not "
            "be swept again: doing so restores whatever key was current then "
            "over anything entered since.\n",
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - worth logging, not worth failing over
        log.warning("could not record that the keychain migration ran", exc_info=True)


def _secrets() -> dict[str, str]:
    """Load the secrets item once, migrating the old per-secret items if needed."""
    global _store, _store_readable
    with _lock:
        if _store is not None and _store_readable:
            return _store
        try:
            raw = _read_item(SECRETS_ACCOUNT)
        except _Unavailable:
            # Never overwrite an item we couldn't read; retry on the next access.
            if _store is None:
                _store = {}
            _store_readable = False
            return _store
        _store_readable = True
        pending = {k: v for k, v in (_store or {}).items() if k in _touched}
        _store = {**(_parse_store(raw) if raw is not None else {}), **pending}
        for account in _deleted:
            _store.pop(account, None)
        if _store.get(MIGRATION_MARKER) != "1" and not _migration_done_on_disk():
            for account in LEGACY_ACCOUNTS:
                if account in _store:
                    continue
                value = _read_password(account)
                if value:
                    _store[account] = value
                    _touched.add(account)
            _store[MIGRATION_MARKER] = "1"
            _touched.add(MIGRATION_MARKER)
            if _save_store():
                _record_migration_done()
        return _store


def _save_store() -> bool:
    """Write the store back, without discarding another process's newer values.

    Studio runs as more than one process and each caches this item. A process
    that loaded its copy minutes ago must not stamp that copy over a secret the
    seller has since entered somewhere else — which is exactly how a key
    entered in Settings kept reverting. Anything this process never touched is
    taken from the item as it stands right now.
    """
    global _store, _store_readable
    with _lock:
        try:
            raw = _read_item(SECRETS_ACCOUNT)
        except _Unavailable:
            # Never overwrite an item we could not read.
            return False
        on_disk = _parse_store(raw) if raw else {}
        merged = {**on_disk, **{k: v for k, v in (_store or {}).items() if k in _touched}}
        for account in list(on_disk):
            if account in _deleted:
                merged.pop(account, None)
        _store, _store_readable = merged, True
        if not _write_password(SECRETS_ACCOUNT, json.dumps(merged)):
            return False
        _touched.clear()
        _deleted.clear()
        return True


def _get(account: str) -> str | None:
    with _lock:
        return _secrets().get(account)


def _set(account: str, value: str) -> bool:
    with _lock:
        _secrets()[account] = value
        _touched.add(account)
        _deleted.discard(account)
        return _save_store()


def _delete(account: str) -> None:
    with _lock:
        if _secrets().pop(account, None) is not None:
            _deleted.add(account)
            _touched.discard(account)
            _save_store()


def get_api_key() -> str | None:
    return _get(KEYRING_ACCOUNT)


def set_api_key(key: str):
    _set(KEYRING_ACCOUNT, key)


def delete_api_key():
    _delete(KEYRING_ACCOUNT)


def get_brave_api_key() -> str | None:
    return _get(BRAVE_ACCOUNT)


def set_brave_api_key(key: str):
    _set(BRAVE_ACCOUNT, key)


def delete_brave_api_key():
    _delete(BRAVE_ACCOUNT)


def get_cursor_api_key() -> str | None:
    return _get(CURSOR_ACCOUNT)


def set_cursor_api_key(key: str):
    _set(CURSOR_ACCOUNT, key)


def delete_cursor_api_key():
    _delete(CURSOR_ACCOUNT)


def get_chatgpt_tokens() -> dict | None:
    raw = _get(CHATGPT_ACCOUNT)
    try:
        tokens = json.loads(raw) if raw else None
    except Exception:
        return None
    return tokens if isinstance(tokens, dict) else None


def set_chatgpt_tokens(tokens: dict):
    # Keep rotated tokens in memory if a save fails, but never report a login
    # as saved when it would disappear on the next restart.
    if not _set(CHATGPT_ACCOUNT, json.dumps(tokens)):
        raise RuntimeError(
            "Could not save the ChatGPT sign-in to macOS Keychain. "
            "Allow Keychain access and try again before restarting Studio."
        )


def delete_chatgpt_tokens():
    _delete(CHATGPT_ACCOUNT)


def _get_json(account: str) -> dict | None:
    raw = _get(account)
    try:
        value = json.loads(raw) if raw else None
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def get_chatgpt_registration() -> dict:
    return _get_json(CHATGPT_REGISTRATION_ACCOUNT) or {}


def set_chatgpt_registration(registration: dict):
    _set(CHATGPT_REGISTRATION_ACCOUNT, json.dumps(registration))


def get_relay_device() -> dict | None:
    device = _get_json(RELAY_DEVICE_ACCOUNT)
    if device and device.get("deviceId") and device.get("token"):
        return device
    return None


def set_relay_device(device: dict):
    _set(RELAY_DEVICE_ACCOUNT, json.dumps(device))


def delete_relay_device():
    _delete(RELAY_DEVICE_ACCOUNT)


def get_marketplace_accounts() -> dict:
    return _get_json(MARKETPLACE_ACCOUNTS_ACCOUNT) or {}


def set_marketplace_account(marketplace: str, account: dict):
    with _lock:
        accounts = get_marketplace_accounts()
        accounts[marketplace] = account
        _set(MARKETPLACE_ACCOUNTS_ACCOUNT, json.dumps(accounts))


def delete_marketplace_account(marketplace: str):
    with _lock:
        accounts = get_marketplace_accounts()
        if accounts.pop(marketplace, None) is None:
            return
        if accounts:
            _set(MARKETPLACE_ACCOUNTS_ACCOUNT, json.dumps(accounts))
        else:
            _delete(MARKETPLACE_ACCOUNTS_ACCOUNT)


def get_ai_keys() -> dict[str, str]:
    keys = _get_json(AI_KEYS_ACCOUNT) or {}
    return {name: value for name, value in keys.items() if isinstance(value, str) and value}


def get_ai_key(provider_id: str) -> str | None:
    return get_ai_keys().get(provider_id)


def set_ai_key(provider_id: str, key: str):
    with _lock:
        keys = get_ai_keys()
        keys[provider_id] = key
        _set(AI_KEYS_ACCOUNT, json.dumps(keys))


def delete_ai_key(provider_id: str):
    with _lock:
        keys = get_ai_keys()
        if keys.pop(provider_id, None) is None:
            return
        if keys:
            _set(AI_KEYS_ACCOUNT, json.dumps(keys))
        else:
            _delete(AI_KEYS_ACCOUNT)


REASONING_LADDER = ("none", "low", "medium", "high", "xhigh", "max")
DEFAULT_REASONING_EFFORT = "low"


def _clean_reasoning_effort(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    effort = value.strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "off": "none",
        "disabled": "none",
        "minimal": "low",
        "extra-high": "xhigh",
        "extrahigh": "xhigh",
    }
    effort = aliases.get(effort, effort)
    return effort if effort in REASONING_LADDER else None


def _clean_model_slug(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    slug = value.strip()
    return slug or None


def get_chatgpt_models() -> dict[str, str]:
    raw = _get(CHATGPT_MODELS_ACCOUNT)
    try:
        payload = json.loads(raw) if raw else None
    except Exception:
        payload = None
    models: dict[str, str] = {}
    if isinstance(payload, dict):
        vision = _clean_model_slug(payload.get("vision_model"))
        listing = _clean_model_slug(payload.get("listing_model"))
        reasoning = _clean_reasoning_effort(payload.get("reasoning_effort"))
        if vision:
            models["vision_model"] = vision
        if listing:
            models["listing_model"] = listing
        if reasoning:
            models["reasoning_effort"] = reasoning
    return models


def set_chatgpt_models(
    *,
    vision_model: str | None = None,
    listing_model: str | None = None,
    reasoning_effort: str | None = None,
):
    current = get_chatgpt_models()
    vision = _clean_model_slug(vision_model)
    listing = _clean_model_slug(listing_model)
    reasoning = _clean_reasoning_effort(reasoning_effort)
    if vision:
        current["vision_model"] = vision
    if listing:
        current["listing_model"] = listing
    if reasoning:
        current["reasoning_effort"] = reasoning
    _set(CHATGPT_MODELS_ACCOUNT, json.dumps(current))


def warm_keychain() -> dict[str, bool]:
    """Load Studio secrets at launch without rewriting the credentials item.

    Call from the desktop main thread before serving requests so the Keychain
    prompt happens at launch (when someone can click Always Allow), not
    mid-generate from a phone/iPad remote session.
    """
    with _lock:
        secrets = _secrets()

    found = {
        "api_key": bool(secrets.get(KEYRING_ACCOUNT)),
        "brave": bool(secrets.get(BRAVE_ACCOUNT)),
        "cursor": bool(secrets.get(CURSOR_ACCOUNT)),
        "chatgpt": bool(secrets.get(CHATGPT_ACCOUNT)),
        "chatgpt_models": bool(secrets.get(CHATGPT_MODELS_ACCOUNT)),
    }
    log.info(
        "keychain warmed api_key=%s brave=%s cursor=%s chatgpt=%s models=%s",
        found["api_key"],
        found["brave"],
        found["cursor"],
        found["chatgpt"],
        found["chatgpt_models"],
    )
    return found
