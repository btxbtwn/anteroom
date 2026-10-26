"""The durable automation clock.

Same shape as T3 Code's server scheduler (a loop with a manual tick tests
can call): due tasks are read from the database, a slot is claimed once,
and a crash resumes that claim instead of inserting another. Missed times
coalesce into one run. Only one automation runs at a time, and it will not
start while a marketplace job already holds the Bridge.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from vendoo_studio.database import SessionLocal
from vendoo_studio.models.automation import Automation, AutomationRun
from vendoo_studio.models.conversation import utcnow
from vendoo_studio.repositories.queries import JobRepo
from vendoo_studio.services.automation_schedule import as_utc
from vendoo_studio.services.automations import (
    STALE_RUN,
    WAITING_APPROVAL,
    DOWN_STATE,
    AutomationBusy,
    actions_for_run,
    apply_local,
    claim_scheduled_run,
    enqueue_approval,
    finish_run,
    log_item_job,
    mark_retry,
    mark_waiting,
    note_relist_result,
    record_application,
    select_items,
)

log = logging.getLogger("vendoo_studio.automations")

TICK_SECONDS = 30.0
_task: asyncio.Task | None = None
# Runs this process is inside. A restart starts empty, so a leftover "running" row is picked up again.
_inflight: set[str] = set()


async def tick(db: Session, *, now: datetime | None = None, executor=None) -> int:
    """Run every due automation that can start. Returns how many runs finished or stepped aside."""
    moment = as_utc(now or datetime.now(UTC))
    handled = 0
    _recover_stale(db, moment)
    while True:
        if _bridge_busy(db):
            return handled
        run = _resume_waiting(db, moment)
        if run is None:
            run = _claim_next(db, moment)
        if run is None:
            return handled
        automation = db.get(Automation, run.automation_id)
        if automation is None or automation.status not in {"active", "paused"}:
            run.status = "failed"
            run.error = "The automation is no longer active."
            run.finished_at = _naive(moment)
            db.commit()
            handled += 1
            continue
        # A paused task still finishes a run that was already claimed. It does not start a new slot.
        if automation.status == "paused" and run.origin == "schedule" and run.attempt_count <= 1 and run.status == "running":
            # Claimed this tick, before a pause could land. Run it: the seller confirmed the slot.
            pass
        try:
            await _execute(db, run, automation, moment, executor)
        except AutomationBusy:
            mark_waiting(db, run, moment)
            return handled + 1
        except Exception as exc:  # noqa: BLE001 - one bad run must not kill the clock
            log.exception("automation run %s failed", run.id)
            mark_retry(db, run, moment, str(exc) or "The run failed.")
            if run.origin == "schedule":
                finish_schedule = run.status == "failed"
                if finish_schedule:
                    finish_run(db, run, automation, moment, status="failed", error=run.error)
            return handled + 1
        handled += 1
        moment = as_utc(now or datetime.now(UTC))


def start_automation_scheduler() -> asyncio.Task:
    """Sweep now, then on an interval. Call from the running event loop."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())
    return _task


def stop_automation_scheduler() -> None:
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
    _task = None


async def _loop() -> None:
    while True:
        db = SessionLocal()
        try:
            await tick(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("automation tick failed")
        finally:
            db.close()
        try:
            await asyncio.sleep(TICK_SECONDS)
        except asyncio.CancelledError:
            raise


def _recover_stale(db: Session, now: datetime) -> None:
    cutoff = _naive(now - STALE_RUN)
    stale = (
        db.query(AutomationRun)
        .filter(AutomationRun.status == "running", AutomationRun.started_at < cutoff)
        .all()
    )
    changed = False
    for run in stale:
        if run.id in _inflight or _bridge_busy(db):
            continue
        run.status = "waiting"
        run.next_attempt_at = _naive(now)
        run.error = "This run was still marked running after a restart. It will try again."
        log.info("requeued stale automation run %s", run.id)
        changed = True
    if changed:
        db.commit()


def _resume_waiting(db: Session, now: datetime) -> AutomationRun | None:
    waiting = (
        db.query(AutomationRun)
        .filter(AutomationRun.status == "waiting")
        .order_by(AutomationRun.next_attempt_at.asc())
        .all()
    )
    for run in waiting:
        due = as_utc(run.next_attempt_at)
        if due is not None and due > now:
            continue
        if run.id in _inflight:
            continue
        run.status = "running"
        db.commit()
        db.refresh(run)
        return run
    return None


def _claim_next(db: Session, now: datetime) -> AutomationRun | None:
    if db.query(AutomationRun).filter(AutomationRun.status.in_(("running", "waiting"))).first():
        return None
    due = [
        row for row in db.query(Automation).filter(Automation.status == "active").all()
        if as_utc(row.next_run_at) is not None and as_utc(row.next_run_at) <= now
    ]
    due.sort(key=lambda row: as_utc(row.next_run_at))
    for row in due:
        run = claim_scheduled_run(db, row, now)
        if run is not None:
            return run
        _skip_claimed_slot(db, row, now)
    return None


def _skip_claimed_slot(db: Session, row: Automation, now: datetime) -> None:
    """A slot that already has a finished run should not stay due forever."""
    from vendoo_studio.services.automation_schedule import advance_after_run

    slot = as_utc(row.next_run_at)
    if slot is None:
        return
    existing = (
        db.query(AutomationRun)
        .filter(AutomationRun.automation_id == row.id, AutomationRun.scheduled_for == _naive(slot))
        .one_or_none()
    )
    if existing is None or existing.status not in {"completed", "failed"}:
        return
    row.next_run_at = _naive(advance_after_run(row.schedule, row.timezone, existing.scheduled_for, now))
    if row.schedule.get("kind") == "once":
        row.status = "completed" if existing.status == "completed" else "paused"
    db.commit()


async def _execute(db: Session, run: AutomationRun, automation: Automation, now: datetime, executor) -> None:
    if executor is None:
        from vendoo_studio.services.automation_actions import perform
        executor = perform
    _inflight.add(run.id)
    try:
        await _execute_items(db, run, automation, now, executor)
    finally:
        _inflight.discard(run.id)


async def _execute_items(db: Session, run: AutomationRun, automation: Automation, now: datetime, executor) -> None:
    actions = actions_for_run(automation)
    spec = {
        "filters": automation.filters or {},
        "actions": actions,
        "marketplaces": automation.marketplaces or [],
    }
    items = select_items(db, spec, now, automation_id=automation.id, persist_price=False)
    summary = dict(run.summary or {"items": [], "job_ids": []})
    done = {item.get("conversation_id") for item in summary.get("items") or [] if item.get("acted")}
    summary.setdefault("items", [])
    summary.setdefault("job_ids", [])
    for item in items:
        if not item["include"] or item["conversation_id"] in done:
            continue
        if not run.dry_run and JobRepo(db).get_active():
            run.summary = summary
            db.commit()
            raise AutomationBusy()
        outcome = await _one_item(db, run, automation, item, executor, actions)
        if outcome.get("busy"):
            run.summary = summary
            db.commit()
            raise AutomationBusy()
        summary["items"].append(outcome)
        if outcome.get("job_id"):
            summary["job_ids"].append(outcome["job_id"])
        run.summary = summary
        db.commit()
    finish_run(db, run, automation, now, status="completed")


async def _one_item(db: Session, run: AutomationRun, automation: Automation, item: dict, executor, actions: list[dict]) -> dict:
    local_actions = [action for action in actions if action["type"] in {"price", "update_fields"}]
    regenerate = next((action for action in actions if action["type"] == "regenerate"), None)
    publish_actions = [action for action in actions if action["type"] in {"delist", "relist"}]
    hold = regenerate is not None and bool(automation.require_approval)
    markets = list(automation.marketplaces or [])
    remote: list[dict] = []
    error = None
    state = None
    down = None
    local = {"ok": True, "revision_id": None, "notes": []}

    if run.dry_run:
        if local_actions:
            local = apply_local(db, item["conversation_id"], local_actions, dry_run=True)
        if local.get("ok") and not local.get("revision_id"):
            local["revision_id"] = _revision_id(db, item["conversation_id"])
        kinds = []
        if regenerate:
            kinds.append("regenerate")
        kinds.extend(action["type"] for action in publish_actions)
        remote = [{"type": kind, "ok": True, "dry_run": True} for kind in kinds]
        if hold:
            state = WAITING_APPROVAL
        error = None if local.get("ok") else local.get("error")
        return _finish_item(db, run, automation, item, local, remote, error=error, state=state, down=down)

    if regenerate:
        stepped = await _run_remote(db, run, automation, item, executor, regenerate, markets, None)
        if stepped.get("busy"):
            return stepped
        remote.append(stepped["remote"])
        error = stepped.get("error")

    if error is None and local_actions:
        local = apply_local(db, item["conversation_id"], local_actions, dry_run=False)
        if not local.get("ok"):
            error = local.get("error")
    if error is None and not local.get("revision_id"):
        local["revision_id"] = _revision_id(db, item["conversation_id"])

    if error is None and hold:
        if not markets:
            error = "Name the marketplaces. An automation does not publish a regenerated listing everywhere by default."
        elif not local.get("revision_id"):
            error = "Regenerate did not save a listing."
        else:
            approval = enqueue_approval(db, automation, run.id, item, local["revision_id"], markets)
            state = WAITING_APPROVAL
            remote.append({"type": "approval", "ok": True, "approval_id": approval.id, "state": state})
    elif error is None:
        for action in publish_actions:
            stepped = await _run_remote(db, run, automation, item, executor, action, markets, local.get("revision_id"))
            if stepped.get("busy"):
                return stepped
            remote.append(stepped["remote"])
            error = stepped.get("error")
            state = stepped.get("state") or state
            down = stepped.get("down") if stepped.get("down") is not None else down
            if error:
                break
        # Approval is off, and regenerate is the only way this task publishes.
        if error is None and regenerate and not publish_actions:
            from vendoo_studio.services.automation_actions import publish_revision

            result = await publish_revision(db, item["conversation_id"], markets, local.get("revision_id"))
            remote.append({"type": "publish", **result})
            note_relist_result(db, automation.id, run.id, item, result)
            if not result.get("ok"):
                error = result.get("error") or "Publish failed."
                state = result.get("state")
                down = result.get("down")

    return _finish_item(db, run, automation, item, local, remote, error=error, state=state, down=down)


async def _run_remote(db, run, automation, item, executor, action, markets, revision_id) -> dict:
    if JobRepo(db).get_active():
        return {
            **_outcome(item, {"notes": []}, [], error="Waiting for the current marketplace job.", dry_run=bool(run.dry_run)),
            "busy": True,
            "acted": False,
        }
    try:
        result = await executor(db, item, action, markets, revision_id)
    except AutomationBusy:
        return {
            **_outcome(item, {"notes": []}, [], error="Waiting for the current marketplace job.", dry_run=bool(run.dry_run)),
            "busy": True,
            "acted": False,
        }
    if action["type"] in {"relist", "delist"} or result.get("down") or result.get("detail"):
        note_relist_result(db, automation.id, run.id, item, result)
    error = None if result.get("ok") else (result.get("error") or f"{action['type']} failed.")
    return {"remote": {"type": action["type"], **result}, "error": error, "state": result.get("state"), "down": result.get("down")}


def _finish_item(db, run, automation, item, local, remote, *, error, state, down) -> dict:
    ok = error is None
    payload = {
        "automation_id": automation.id,
        "run_id": run.id,
        "dry_run": bool(run.dry_run),
        "title": item.get("title"),
        "actions": [action["type"] for action in (automation.actions or [])],
        "marketplaces": item.get("marketplaces"),
        "price": item.get("price"),
        "next_price": local.get("price", item.get("next_price")),
        "original_price": item.get("original_price"),
        "notes": local.get("notes") or [],
        "remote": remote,
        "state": state,
        "down": down or [],
    }
    job_id = log_item_job(db, item["conversation_id"], local.get("revision_id"), payload, ok=ok, error=error)
    if ok and not run.dry_run and state != DOWN_STATE and (automation.filters or {}).get("min_age_days"):
        listed = _parse_time(item.get("listed_at"))
        record_application(db, automation.id, item["conversation_id"], listed, utcnow())
    return _outcome(item, local, remote, error=error, job_id=job_id, dry_run=bool(run.dry_run), state=state, down=down)


def _revision_id(db: Session, conv_id: str) -> str | None:
    from vendoo_studio.services.automations import _current

    revision, _listing = _current(db, conv_id)
    return revision.id if revision is not None else None


def _outcome(item, local, remote, *, error, job_id=None, busy=False, dry_run=False, state=None, down=None) -> dict:
    return {
        "conversation_id": item["conversation_id"],
        "title": item.get("title"),
        "ok": error is None and not busy,
        "error": error,
        "state": state,
        "down": list(down or []),
        "acted": True,
        "job_id": job_id,
        "price": item.get("price"),
        "next_price": local.get("price", item.get("next_price")) if isinstance(local, dict) else item.get("next_price"),
        "original_price": item.get("original_price"),
        "notes": local.get("notes") if isinstance(local, dict) else [],
        "remote": remote,
        "dry_run": bool(dry_run),
    }


def _bridge_busy(db: Session) -> bool:
    return bool(JobRepo(db).get_active())


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed


def _naive(value: datetime) -> datetime:
    return as_utc(value).replace(tzinfo=None)
