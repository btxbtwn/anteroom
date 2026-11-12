"""Browser crash reports: the web app posts uncaught errors here.

On the hosted app the seller session is required, so each report carries the
seller id, and ``services/error_alerts.py`` emails it to the operator. On the
Mac the report only lands in Settings → Logs.
"""
import logging
import os
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/error-reports", tags=["diagnostics"])
log = logging.getLogger("vendoo_studio.browser")


@router.get('/delivery')
def delivery_status(request: Request):
    """Only the configured operator sees shared mail accounting, never reports."""
    operator = os.environ.get('ANTEROOM_OPERATOR_USER_ID')
    seller = getattr(request.state, 'seller', None)
    if not operator or seller is None or seller.user_id != operator:
        return {'visible': False}
    from vendoo_studio.services.error_alerts import _handler

    if _handler is None:
        return {'visible': True, 'state': 'disabled'}
    return {'visible': True, **_handler.delivery.status(), 'dropped': _handler.dropped}


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
def report(body: BrowserFailure):
    log.error("Browser %s: %s", body.kind, body.message[:200], extra={"browser": body.model_dump()})
    return {"accepted": True}
