"""Marketplace actions called only after a seller confirms a specific proposal.

The scheduler never calls these writes. One explicit confirmation makes one
attempt per named marketplace; a failed result stays visible for recovery.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal
from vendoo_studio.repositories.queries import JobRepo
from vendoo_studio.services.automations import DOWN_STATE, AutomationBusy

log = logging.getLogger("vendoo_studio.automations")

# How long one item waits for the Bridge slot to clear before this run steps aside.
IDLE_TIMEOUT_S = 8 * 60


async def perform(db: Session, item: dict, action: dict, marketplaces: list[str], revision_id: str | None, *, confirm: bool = False) -> dict:
    if action["type"] in {"delist", "relist"} and confirm is not True:
        return {"ok": False, "error": "Review and confirm this item’s marketplace action first."}
    if JobRepo(db).get_active():
        raise AutomationBusy()
    kind = action["type"]
    if kind == "delist":
        return await _delist(db, item["conversation_id"], marketplaces)
    if kind == "relist":
        return await _relist(db, item["conversation_id"], marketplaces, revision_id)
    if kind == "regenerate":
        return await _regenerate(item["conversation_id"])
    return {"ok": True, "skipped": kind}


async def _delist(db: Session, conv_id: str, marketplaces: list[str]) -> dict:
    from vendoo_studio.services.marketplace_delist import DelistError, delist

    try:
        result = await delist(db, conv_id, marketplaces, True)
    except DelistError as exc:
        if exc.status_code == 409 and "another automation" in str(exc).lower():
            raise AutomationBusy() from exc
        return {"ok": False, "error": str(exc)}
    if not await _wait_until_idle(db):
        return {"ok": False, "error": "The delist was still running when this automation stopped waiting."}
    failed = {name: (result.get(name) or {}).get("error") or "The marketplace did not confirm deletion."
              for name in marketplaces if (result.get(name) or {}).get("state") != "deleted"}
    if failed:
        return {"ok": False, "error": "; ".join(f"{name}: {error}" for name, error in failed.items()), "errors": failed, "detail": result}
    return {"ok": True, "detail": result}


async def _relist(db: Session, conv_id: str, marketplaces: list[str], revision_id: str | None) -> dict:
    """Take the named listings down, then list those same items again.

    A marketplace that fails to list does not stop the others. Each is tried
    once. Anything still down is reported as ``down, needs relist``
    and is not treated as a reason to skip the marketplaces that already succeeded.
    A delist that itself fails returns before anything is marked down, because
    the listings may still be up.
    """
    taken_down = await _delist(db, conv_id, marketplaces)
    if not taken_down.get("ok"):
        confirmed = [name for name in marketplaces if (taken_down.get("detail", {}).get(name) or {}).get("state") == "deleted"]
        if confirmed:
            return {**taken_down, "state": DOWN_STATE, "down": confirmed}
        return taken_down
    return await _list_remaining(db, conv_id, marketplaces, revision_id)


async def publish_revision(db: Session, conv_id: str, marketplaces: list[str], revision_id: str | None) -> dict:
    """List a saved revision on named marketplaces after explicit approval.

    An existing live listing needs a separate Relist approval; List never
    takes it down as a side effect. Sold marketplaces are left alone.
    """
    if not marketplaces:
        return {"ok": False, "error": "Name the marketplaces. An automation does not publish everywhere by default."}
    if not revision_id:
        return {"ok": False, "error": "Publish needs the listing that was just saved."}
    live, fresh = _partition(db, conv_id, marketplaces)
    detail: list[dict] = []
    down: list[str] = []
    errors: dict[str, str] = {}
    hard_error = None
    if live:
        return {"ok": False, "error": "Already listed on " + ", ".join(live) + ". Choose and approve Relist explicitly."}
    if fresh:
        listed = await _list_remaining(db, conv_id, fresh, revision_id)
        detail.extend(listed.get("detail") or [])
        down.extend(listed.get("down") or [])
        errors.update(listed.get("errors") or {})
        if not listed.get("ok") and not listed.get("down"):
            hard_error = hard_error or listed.get("error") or "Relist failed."
    if down:
        return {
            "ok": False,
            "state": DOWN_STATE,
            "down": down,
            "error": _down_message(down),
            "detail": detail,
            "errors": errors,
        }
    if hard_error:
        return {"ok": False, "error": hard_error, "detail": detail}
    return {"ok": True, "detail": detail}


def _partition(db: Session, conv_id: str, marketplaces: list[str]) -> tuple[list[str], list[str]]:
    """Classify live and fresh targets, excluding marketplaces where the item sold."""
    from vendoo_studio.services.marketplace_delist import DelistError, listings

    try:
        known = listings(db, conv_id)
    except DelistError:
        known = {}
    live = []
    fresh = []
    for name in marketplaces:
        entry = known.get(name) or {}
        if entry.get("sold_here"):
            continue
        if entry.get("listing_id") and entry.get("state") != "deleted":
            live.append(name)
        else:
            fresh.append(name)
    return live, fresh


async def _list_remaining(db: Session, conv_id: str, marketplaces: list[str], revision_id: str | None) -> dict:
    if not revision_id:
        return {"ok": False, "error": "Relist needs the listing that was just saved."}
    pending = list(marketplaces)
    detail: list[dict] = []
    errors: dict[str, str] = {}
    still = []
    for name in pending:
        outcome = await _list_one(db, conv_id, name, revision_id)
        if outcome.get("ok"):
            detail.append(outcome)
        else:
            errors[name] = outcome.get("error") or "Relist failed."
            still.append(name)
    pending = still
    if pending:
        return {
            "ok": False,
            "state": DOWN_STATE,
            "down": pending,
            "error": _down_message(pending),
            "detail": detail,
            "errors": errors,
        }
    return {"ok": True, "detail": detail}


async def _list_one(db: Session, conv_id: str, name: str, revision_id: str) -> dict:
    from vendoo_studio.services.native_marketplaces import ListRequest, NativeMarketplaceError, list_now

    if JobRepo(db).get_active() and not await _wait_until_idle(db):
        return {"ok": False, "marketplace": name, "error": f"The {name} relist was still running when this automation stopped waiting."}
    try:
        # This call follows the seller's consumed item-specific approval.
        result = await list_now(conv_id, name, ListRequest(revision_id=revision_id, confirm=True), db)
    except NativeMarketplaceError as exc:
        return {"ok": False, "marketplace": name, "error": str(exc)}
    if not await _wait_until_idle(db):
        return {"ok": False, "marketplace": name, "error": f"The {name} relist was still running when this automation stopped waiting."}
    db.expire_all()
    job = JobRepo(db).get(result["job_id"]) if result.get("job_id") else None
    if (job is not None and job.status != "completed") or (job is None and result.get("state") not in {"published", "pending_review"}):
        return {"marketplace": name, "ok": False,
                "error": (job.last_error if job else result.get("error")) or "The marketplace did not confirm publication."}
    return {"marketplace": name, "ok": True, "detail": _brief(result)}


def _down_message(names: list[str]) -> str:
    return f"{', '.join(names)}: {DOWN_STATE}"


async def _regenerate(conv_id: str) -> dict:
    """Start a new draft from the photos. Uses its own session: generate closes the one it is given."""
    from fastapi import HTTPException

    from vendoo_studio.routes.chat import generate_listing
    from vendoo_studio.services.streaming import active_generation

    db = SessionLocal()
    try:
        await generate_listing(conv_id, False, db)
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    except Exception as exc:  # noqa: BLE001 - recorded on the item, the rest of the run continues
        log.exception("automation regenerate failed for %s", conv_id)
        return {"ok": False, "error": str(exc) or "Regenerate did not start."}
    finally:
        try:
            db.close()
        except Exception:
            pass
    deadline = asyncio.get_running_loop().time() + IDLE_TIMEOUT_S
    while active_generation(conv_id) is not None:
        if asyncio.get_running_loop().time() > deadline:
            return {"ok": False, "error": "Regenerate was still running when this automation stopped waiting."}
        await asyncio.sleep(0.5)
    return {"ok": True}


async def _wait_until_idle(db: Session) -> bool:
    deadline = asyncio.get_running_loop().time() + IDLE_TIMEOUT_S
    while JobRepo(db).get_active():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.5)
        db.expire_all()
    return True


def _brief(result: dict) -> dict:
    if not isinstance(result, dict):
        return {}
    return {key: result.get(key) for key in ("ok", "state", "job_id", "status") if key in result}
