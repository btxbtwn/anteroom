"""Subscription usage limits for each listing provider, as percentages and reset times.

Ported from T3 Code (MIT, © 2026 T3 Tools Inc.):
apps/server/src/provider/claudeUsageLimits.ts, apps/server/src/provider/codexUsageLimits.ts,
apps/server/src/usage/cliproxyApi.ts, packages/provider-cursor/src/server/usageLimits.ts and
packages/provider-core/src/server/usageLimits.ts.

Each provider reports the same shape (T3's ServerProviderUsageLimits)::

    {provider, label, checked_at, windows: [{id, kind, label, used_percent,
     resets_at?, window_duration_mins?, description?}], plan?,
     unavailable?: {reason: "unsupported" | "probeFailed", message?}}

Only credentials Studio already uses are read: the ChatGPT sign-in in Keychain,
the seller's own Claude Code login (through the Agent SDK, never a token), and
the Cursor API key. No token ever reaches a response or a log line.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger("vendoo_studio.usage_limits")

PROVIDER_ORDER = ("chatgpt", "claude", "cursor", "mimo")
PROVIDER_LABELS = {"chatgpt": "ChatGPT", "claude": "Claude", "cursor": "Cursor", "mimo": "Xiaomi MiMo"}

SESSION_MINS = 5 * 60
WEEK_MINS = 7 * 24 * 60
KIND_ORDER = {"session": 0, "weekly": 1, "monthly": 2, "other": 3}

SUCCESS_TTL_S = 60.0
FAILURE_TTL_S = 15.0
PROBE_TIMEOUT_S = 20.0

CURSOR_API = "https://api2.cursor.sh"
CURSOR_EXCHANGE_URL = f"{CURSOR_API}/auth/exchange_user_api_key"
CURSOR_USAGE_URL = f"{CURSOR_API}/aiserver.v1.DashboardService/GetCurrentPeriodUsage"

CURSOR_USAGE_WINDOWS = (
    ("totalPercentUsed", "Overall", "Combined usage across both allowances, not a third quota."),
    ("autoPercentUsed", "Cursor Models", "Grok and Composer use this first. Auto can use either pool."),
    (
        "apiPercentUsed",
        "Other Models",
        "Claude, GPT, and Gemini use this pool. Grok and Composer fall back here.",
    ),
)

CLAUDE_PLAN_LABELS = {"pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Enterprise"}


# ---------------------------------------------------------------------------
# Shared model (provider-core/server/usageLimits.ts)


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def clamp_percent(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return 0.0
    return float(max(0.0, min(100.0, value)))


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sort_windows(windows: list[dict]) -> list[dict]:
    return sorted(windows, key=lambda window: (KIND_ORDER.get(window["kind"], 3), window["id"]))


def make_limits(provider: str, checked_at: str, windows: list[dict], plan: str | None = None) -> dict:
    result: dict[str, Any] = {
        "provider": provider,
        "label": PROVIDER_LABELS[provider],
        "checked_at": checked_at,
        "windows": sort_windows(windows),
    }
    if plan:
        result["plan"] = plan
    return result


def make_unavailable(
    provider: str,
    checked_at: str,
    reason: str,
    message: str | None = None,
    plan: str | None = None,
) -> dict:
    result = make_limits(provider, checked_at, [], plan)
    result["unavailable"] = {"reason": reason, **({"message": message} if message else {})}
    return result


def iso_from_epoch_seconds(value: object) -> str | None:
    if not _finite(value) or value <= 0:  # type: ignore[operator]
        return None
    try:
        return datetime.fromtimestamp(float(value), UTC).isoformat().replace("+00:00", "Z")  # type: ignore[arg-type]
    except (OverflowError, OSError, ValueError):
        return None


def iso_from_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# ChatGPT


async def fetch_chatgpt() -> dict:
    """ChatGPT's plan usage is not readable with Sign in with ChatGPT.

    OpenAI tracks it across every app the seller connects, on its own usage
    page, as T3 Code tells its users too (CodexManagedProvider.ts).
    """
    from vendoo_studio.services.chatgpt_oauth import chatgpt_signed_in

    checked_at = now_iso()
    if not chatgpt_signed_in():
        return make_unavailable("chatgpt", checked_at, "unsupported", "Sign in to ChatGPT in Settings → Providers.")
    return make_unavailable(
        "chatgpt",
        checked_at,
        "unsupported",
        "ChatGPT tracks plan usage across the apps you connect. See it in ChatGPT → Settings → Usage.",
    )


# ---------------------------------------------------------------------------
# Claude (claudeUsageLimits.ts)

CLAUDE_WINDOWS = {
    "five_hour": ("session", "Session", SESSION_MINS),
    "seven_day": ("weekly", "Weekly", WEEK_MINS),
}


def _scoped_window_id(display_name: str) -> str:
    slug = "".join(char if char.isalnum() else "_" for char in display_name.lower())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return f"seven_day_{slug}"


def _percent_from_amounts(used: object, limit: object) -> float | None:
    if not _finite(used) or not _finite(limit) or used < 0 or limit <= 0:  # type: ignore[operator]
        return None
    percent = used / limit * 100  # type: ignore[operator]
    return clamp_percent(percent) if math.isfinite(percent) else None


def _claude_spend_percent(rate_limits: dict) -> float | None:
    spend = rate_limits.get("spend")
    if isinstance(spend, dict) and spend.get("enabled") is True:
        used = spend.get("used") if isinstance(spend.get("used"), dict) else None
        limit = spend.get("limit") if isinstance(spend.get("limit"), dict) else None
        if (
            used
            and limit
            and isinstance(used.get("currency"), str)
            and used["currency"].strip()
            and used["currency"].strip() == str(limit.get("currency", "")).strip()
            and used.get("exponent") == limit.get("exponent")
        ):
            percent = _percent_from_amounts(used.get("amount_minor"), limit.get("amount_minor"))
            if percent is not None:
                return percent
    extra = rate_limits.get("extra_usage")
    if not isinstance(extra, dict) or extra.get("is_enabled") is not True:
        return None
    percent = _percent_from_amounts(extra.get("used_credits"), extra.get("monthly_limit"))
    if percent is None:
        percent = extra.get("utilization")
    return clamp_percent(percent) if _finite(percent) and percent >= 0 else None  # type: ignore[operator]


def _claude_model_scoped(rate_limits: dict) -> list[dict]:
    """`model_scoped` where the CLI sends it, else the `weekly_scoped` entries of `limits`."""
    raw = rate_limits.get("model_scoped")
    if isinstance(raw, list):
        return [
            {
                "display_name": entry["display_name"],
                "utilization": entry.get("utilization"),
                "resets_at": entry.get("resets_at"),
            }
            for entry in raw
            if isinstance(entry, dict) and isinstance(entry.get("display_name"), str)
        ]
    scoped: list[dict] = []
    for limit in rate_limits.get("limits") or []:
        if not isinstance(limit, dict) or limit.get("kind") != "weekly_scoped":
            continue
        scope = limit.get("scope") if isinstance(limit.get("scope"), dict) else {}
        model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
        name = model.get("display_name")
        if isinstance(name, str) and name.strip():
            scoped.append({
                "display_name": name.strip(),
                "utilization": limit.get("percent"),
                "resets_at": limit.get("resets_at"),
            })
    return scoped


def claude_usage_to_limits(response: object, checked_at: str) -> dict:
    """Percentages on the `get_usage` response are already 0–100."""
    data = response if isinstance(response, dict) else {}
    plan_raw = data.get("subscription_type")
    plan = CLAUDE_PLAN_LABELS.get(plan_raw, plan_raw.title()) if isinstance(plan_raw, str) and plan_raw else None
    rate_limits = data.get("rate_limits")
    if not data.get("rate_limits_available") or not isinstance(rate_limits, dict):
        reason = "probeFailed" if data.get("rate_limits_available") else "unsupported"
        message = None if reason == "probeFailed" else "This Claude account has no subscription limits."
        return make_unavailable("claude", checked_at, reason, message, plan)
    windows: list[dict] = []
    for window_id, (kind, label, mins) in CLAUDE_WINDOWS.items():
        window = rate_limits.get(window_id)
        if not isinstance(window, dict) or not _finite(window.get("utilization")):
            continue
        entry: dict[str, Any] = {
            "id": window_id,
            "kind": kind,
            "label": label,
            "used_percent": clamp_percent(window["utilization"]),
            "window_duration_mins": mins,
        }
        resets_at = iso_from_string(window.get("resets_at"))
        if resets_at:
            entry["resets_at"] = resets_at
        windows.append(entry)
    for scoped in _claude_model_scoped(rate_limits):
        if not _finite(scoped["utilization"]):
            continue
        entry = {
            "id": _scoped_window_id(scoped["display_name"]),
            "kind": "weekly",
            "label": f"Weekly · {scoped['display_name']}",
            "used_percent": clamp_percent(scoped["utilization"]),
            "window_duration_mins": WEEK_MINS,
        }
        resets_at = iso_from_string(scoped["resets_at"])
        if resets_at:
            entry["resets_at"] = resets_at
        windows.append(entry)
    spend = _claude_spend_percent(rate_limits)
    if spend is not None:
        windows.append({"id": "monthly_spend", "kind": "monthly", "label": "Monthly spend", "used_percent": spend})
    return make_limits("claude", checked_at, windows, plan)


async def _claude_get_usage() -> dict:
    """Ask the seller's Claude Code for its usage through the Agent SDK control
    channel. No prompt is sent, so it costs nothing."""
    from claude_agent_sdk import ClaudeSDKClient

    from vendoo_studio.providers.claude_agent import _isolated_options

    async with ClaudeSDKClient(_isolated_options(tools=[])) as client:
        query = getattr(client, "_query", None)
        if query is None:
            raise RuntimeError("Claude Agent SDK did not open a session")
        return await query._send_control_request({"subtype": "get_usage"})


async def fetch_claude() -> dict:
    from vendoo_studio.services import claude_auth

    checked_at = now_iso()
    signed_in = await asyncio.to_thread(claude_auth.claude_signed_in)
    if not signed_in:
        return make_unavailable("claude", checked_at, "unsupported", "Sign in to Claude in Settings → Providers.")
    try:
        response = await asyncio.wait_for(_claude_get_usage(), PROBE_TIMEOUT_S)
    except TimeoutError:
        return make_unavailable("claude", checked_at, "probeFailed", "Claude did not report usage in time.")
    except Exception as exc:  # noqa: BLE001
        log.info("Claude usage request failed (%s)", type(exc).__name__)
        return make_unavailable("claude", checked_at, "probeFailed", "Claude could not read usage limits.")
    return claude_usage_to_limits(response, checked_at)


# ---------------------------------------------------------------------------
# Cursor (provider-cursor/src/server/usageLimits.ts)


def cursor_usage_to_limits(body: object, checked_at: str) -> dict:
    """Cursor's dashboard percentages include bonus usage; spend / limit does not."""
    data = body if isinstance(body, dict) else {}
    resets_at = None
    try:
        cycle_end = float(data.get("billingCycleEnd"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        cycle_end = 0.0
    if math.isfinite(cycle_end) and cycle_end > 0:
        resets_at = iso_from_epoch_seconds(cycle_end / 1000)
    plan_usage = data.get("planUsage") if isinstance(data.get("planUsage"), dict) else None
    windows: list[dict] = []
    if plan_usage:
        for window_id, label, description in CURSOR_USAGE_WINDOWS:
            used = plan_usage.get(window_id)
            if not _finite(used):
                continue
            entry: dict[str, Any] = {
                "id": window_id,
                "kind": "monthly",
                "label": label,
                "description": description,
                "used_percent": clamp_percent(used),
            }
            if resets_at:
                entry["resets_at"] = resets_at
            windows.append(entry)
    if not windows:
        return make_unavailable("cursor", checked_at, "unsupported", "This Cursor account reports no plan usage.")
    # Keep T3's Overall → Cursor Models → Other Models order (all monthly).
    order = {window_id: index for index, (window_id, _, _) in enumerate(CURSOR_USAGE_WINDOWS)}
    result = make_limits("cursor", checked_at, [])
    result["windows"] = sorted(windows, key=lambda window: order[window["id"]])
    return result


async def fetch_cursor() -> dict:
    from vendoo_studio.services.keychain import get_cursor_api_key

    checked_at = now_iso()
    api_key = (get_cursor_api_key() or "").strip()
    if not api_key:
        return make_unavailable("cursor", checked_at, "unsupported", "Add a Cursor API key in Settings → Providers.")
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
            # The Cursor SDK makes this same exchange on every run.
            exchange = await client.post(
                CURSOR_EXCHANGE_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                content="{}",
            )
            if exchange.status_code >= 400:
                log.info("Cursor key exchange failed (HTTP %s)", exchange.status_code)
                return make_unavailable(
                    "cursor",
                    checked_at,
                    "probeFailed",
                    f"Cursor could not read usage limits (HTTP {exchange.status_code}).",
                )
            access = exchange.json().get("accessToken")
            if not isinstance(access, str) or not access:
                return make_unavailable("cursor", checked_at, "probeFailed", "Cursor could not read usage limits.")
            response = await client.post(
                CURSOR_USAGE_URL,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Content-Type": "application/json",
                    "connect-protocol-version": "1",
                },
                content="{}",
            )
        if response.status_code >= 400:
            log.info("Cursor usage request failed (HTTP %s)", response.status_code)
            return make_unavailable(
                "cursor",
                checked_at,
                "probeFailed",
                f"Cursor could not read usage limits (HTTP {response.status_code}).",
            )
        return cursor_usage_to_limits(response.json(), checked_at)
    except Exception as exc:  # noqa: BLE001
        log.info("Cursor usage request failed (%s)", type(exc).__name__)
        return make_unavailable("cursor", checked_at, "probeFailed", "Cursor could not read usage limits.")


async def fetch_mimo() -> dict:
    return make_unavailable("mimo", now_iso(), "unsupported", "Xiaomi MiMo does not report usage limits.")


# ---------------------------------------------------------------------------
# Cache and entry point

FETCHERS = {
    "chatgpt": fetch_chatgpt,
    "claude": fetch_claude,
    "cursor": fetch_cursor,
    "mimo": fetch_mimo,
}

_cache: dict[str, tuple[float, dict]] = {}
_locks: dict[str, asyncio.Lock] = {}


def clear_cache() -> None:
    _cache.clear()
    _locks.clear()


def _fresh(entry: tuple[float, dict] | None) -> dict | None:
    if entry is None:
        return None
    stored_at, limits = entry
    ttl = FAILURE_TTL_S if limits.get("unavailable", {}).get("reason") == "probeFailed" else SUCCESS_TTL_S
    return limits if time.monotonic() - stored_at < ttl else None


async def provider_usage_limits(provider: str, *, force: bool = False) -> dict:
    if not force and (cached := _fresh(_cache.get(provider))):
        return cached
    lock = _locks.setdefault(provider, asyncio.Lock())
    async with lock:
        if not force and (cached := _fresh(_cache.get(provider))):
            return cached
        try:
            limits = await asyncio.wait_for(FETCHERS[provider](), PROBE_TIMEOUT_S + 5)
        except Exception as exc:  # noqa: BLE001
            log.info("%s usage probe failed (%s)", provider, type(exc).__name__)
            limits = make_unavailable(
                provider, now_iso(), "probeFailed", f"{PROVIDER_LABELS[provider]} could not read usage limits."
            )
        _cache[provider] = (time.monotonic(), limits)
        return limits


async def all_usage_limits(*, force: bool = False) -> list[dict]:
    return list(
        await asyncio.gather(*(provider_usage_limits(provider, force=force) for provider in PROVIDER_ORDER))
    )
