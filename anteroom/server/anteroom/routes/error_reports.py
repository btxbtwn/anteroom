"""Browser crash reports: the web app posts uncaught errors here.

On the hosted app the seller session is required, so each report carries the
seller id, and ``services/error_alerts.py`` emails it to the operator. On the
Mac the report only lands in Settings → Logs.
"""
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from anteroom.database import engine
from anteroom.repositories.error_email import EmailStore
from anteroom.services.error_alerts import current_release

router = APIRouter(prefix="/api/error-reports", tags=["diagnostics"])
log = logging.getLogger("anteroom.browser")

GroupStatus = Literal['open', 'resolved', 'muted']
Severity = Literal['WARNING', 'ERROR', 'CRITICAL']
NO_NUL = r'^[^\x00]*$'


def _is_operator(request: Request) -> bool:
    operator = os.environ.get('ANTEROOM_OPERATOR_USER_ID')
    seller = getattr(request.state, 'seller', None)
    return bool(operator and seller is not None and seller.user_id == operator)


@router.get('/delivery')
def delivery_status(request: Request):
    """Only the configured operator sees shared mail accounting, never reports."""
    if not _is_operator(request):
        return {'visible': False}
    from anteroom.services.error_alerts import _handler

    if _handler is None:
        return {'visible': True, 'state': 'disabled'}
    return {'visible': True, **_handler.delivery.status(), 'dropped': _handler.dropped}


def _operator_store(request: Request) -> EmailStore:
    if not _is_operator(request):
        raise HTTPException(status_code=403, detail='Operator access required')
    store = EmailStore(engine)
    if not store.lifecycle_active():
        raise HTTPException(status_code=503, detail='Error group triage has not been activated')
    return store


class ResolveGroup(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    resolved_release: str | None = Field(default=None, min_length=1, max_length=255, pattern=NO_NUL)


class MuteGroup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    duration_hours: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=500, pattern=NO_NUL)


@router.get('/groups')
def list_groups(store: EmailStore = Depends(_operator_store), status: GroupStatus = 'open',
                severity: Severity | None = None, sort: Literal['last_seen', 'count'] = 'last_seen',
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return store.list_groups(status, severity=severity, sort=sort, limit=limit, offset=offset)


def _triage(store: EmailStore, severity: Severity, fingerprint: str, status: GroupStatus, **fields):
    if '\x00' in fingerprint or not store.triage(severity, fingerprint, status=status, **fields):
        raise HTTPException(status_code=404, detail='Error group not found')
    return {'status': status}


@router.post('/groups/{severity}/{fingerprint}/resolve')
def resolve_group(severity: Severity, fingerprint: str, body: ResolveGroup | None = None,
                  store: EmailStore = Depends(_operator_store)):
    return _triage(store, severity, fingerprint, 'resolved', resolved_at=datetime.now(UTC).timestamp(),
                   resolved_release=(body.resolved_release if body else None) or current_release())


@router.post('/groups/{severity}/{fingerprint}/mute')
def mute_group(severity: Severity, fingerprint: str, body: MuteGroup | None = None,
               store: EmailStore = Depends(_operator_store)):
    body = body or MuteGroup()
    until = None
    if body.duration_hours is not None:
        try:
            until = (datetime.now(UTC) + timedelta(hours=body.duration_hours)).timestamp()
        except OverflowError as exc:
            raise HTTPException(status_code=422, detail='Mute duration is too large') from exc
    return _triage(store, severity, fingerprint, 'muted', muted_until=until,
                   **body.model_dump(include={'note'}, exclude_unset=True))


@router.post('/groups/{severity}/{fingerprint}/reopen')
def reopen_group(severity: Severity, fingerprint: str, store: EmailStore = Depends(_operator_store)):
    return _triage(store, severity, fingerprint, 'open')


class BrowserFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["crash", "unhandled-rejection", "render", "query", "mutation"]
    name: str | None = Field(default=None, max_length=200)
    message: str = Field(default="", max_length=4000)
    stack: str | None = Field(default=None, max_length=16000)
    # Pathname only; the web app strips the query and hash before sending.
    path: str = Field(default="-", max_length=500)
    user_agent: str | None = Field(default=None, max_length=500)
    release: str | None = Field(default=None, max_length=50)


@router.post("", status_code=202)
def report(body: BrowserFailure, request: Request):
    limiter = getattr(request.app.state, "error_report_limit", None)
    seller = getattr(request.state, "seller", None)
    if limiter is not None and seller is not None:
        limiter.take(seller.user_id)
    log.error("Browser %s: %s", body.kind, body.message[:200], extra={"browser": body.model_dump()})
    return {"accepted": True}
