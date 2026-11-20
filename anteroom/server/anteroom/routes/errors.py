"""Listings a relist left offline.

The command is ``anteroom errors list``. Each open row is one marketplace
that is still down and needs a relist.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import automations

router = APIRouter(prefix="/api/errors", tags=["errors"])


@router.get("")
def list_errors(db: Session = Depends(get_db)):
    """Listings a relist left down. Each one still needs a relist."""
    return automations.list_open_gaps(db)
