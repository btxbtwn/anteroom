"""Serialize account mode changes with marketplace job claims, never network work."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select, text

from vendoo_studio.models.job import ACTIVE_JOB_STATUSES, Job
from vendoo_studio.models.user_preference import UserPreference
from vendoo_studio.services import user_settings
from vendoo_studio.services.api_job_lock import start_gate
from vendoo_studio.services.service_context import current

ListingMode = Literal["direct", "vendoo"]


def read(db=None) -> ListingMode:
    # A hosted request's settings snapshot may predate another device's switch.
    context = current()
    if db is None and context is not None:
        with context.session_factory() as session:
            return read(session)
    if db is not None and context is not None:
        payload = db.scalar(select(UserPreference.value).where(UserPreference.key == "settings")) or {}
    else:
        payload = user_settings.read_settings()
    ui = payload.get("ui") if isinstance(payload, dict) else None
    return "vendoo" if isinstance(ui, dict) and ui.get("listing_mode") == "vendoo" else "direct"


def require(expected: ListingMode, db=None) -> None:
    if read(db) != expected:
        name = "Vendoo" if expected == "vendoo" else "Direct marketplace"
        raise HTTPException(409, f"{name} actions are disabled by your listing mode in Settings.")


@contextmanager
def guard(db, expected: ListingMode | None = None):
    with start_gate():
        context = current()
        if db.bind.dialect.name == "postgresql":
            owner = context.seller_id if context else "local"
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                       {"identity": f"anteroom-listing-mode:{owner}"})
        if expected is not None:
            require(expected, db)
        yield


def change(db, mode: ListingMode) -> None:
    with guard(db):
        if read(db) == mode:
            return
        if db.scalar(select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)):
            raise HTTPException(409, "Wait for the current marketplace action to finish before changing listing mode.")
        user_settings.set_ui_prefs(listing_mode=mode)
        db.commit()
