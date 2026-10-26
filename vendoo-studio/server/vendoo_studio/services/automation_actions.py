"""Marketplace steps an automation is allowed to take.

Delist and relist call the same seller-confirmed paths a person uses. The
confirmation is the automation the seller created and confirmed, and only
for the marketplaces that task names. Price and field edits are saved on
the listing first, by the caller, and are not published here by themselves.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal
from vendoo_studio.repositories.queries import JobRepo
from vendoo_studio.services.automations import AutomationBusy

log = logging.getLogger("vendoo_studio.automations")

# How long one item waits for the Bridge slot to clear before this run steps aside.
IDLE_TIMEOUT_S = 8 * 60


async def perform(db: Session, item: dict, action: dict, marketplaces: list[str], revision_id: str | None) -> dict:
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
    return {"ok": True, "detail": _brief(result)}


async def _relist(db: Session, conv_id: str, marketplaces: list[str], revision_id: str | None) -> dict:
    """Take the named listings down, then list those same items again. List reuses the saved marketplace id."""
    from vendoo_studio.services.native_marketplaces import ListRequest, NativeMarketplaceError, list_now

    taken_down = await _delist(db, conv_id, marketplaces)
    if not taken_down.get("ok"):
        return taken_down
    if not revision_id:
        return {"ok": False, "error": "Relist needs the listing that was just saved."}
    results = []
    for name in marketplaces:
        if JobRepo(db).get_active():
            if not await _wait_until_idle(db):
                raise AutomationBusy()
        try:
            # confirm=True is the seller's confirmed automation, for this marketplace only.
            result = await list_now(conv_id, name, ListRequest(revision_id=revision_id, confirm=True), db)
        except NativeMarketplaceError as exc:
            return {"ok": False, "error": str(exc), "marketplace": name}
        results.append({"marketplace": name, "ok": True, "detail": _brief(result)})
        if not await _wait_until_idle(db):
            return {"ok": False, "error": f"The {name} relist was still running when this automation stopped waiting."}
    return {"ok": True, "detail": results}


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
