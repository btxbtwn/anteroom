"""Shared expense connection API for desktop and hosted sellers."""
from collections.abc import Awaitable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from anteroom.database import get_db
from anteroom.services import plaid_expenses as expenses
from anteroom.services.hosted_context import CredentialStorageUnavailable
from anteroom.services.plaid_client import PlaidClient, PlaidError
from anteroom.services.service_context import current

router = APIRouter(prefix="/api/business-expenses/plaid", tags=["business-expenses"])


class PlaidLinkToken(BaseModel):
    link_token: str
    redirect_uri: str | None = None


class PlaidExchange(BaseModel):
    public_token: str = Field(min_length=1, max_length=1024, repr=False)
    institution_name: str = Field(default="Connected institution", min_length=1, max_length=256)


def _client() -> PlaidClient:
    client = PlaidClient.configured()
    if client is None:
        raise HTTPException(503, "Set PLAID_CLIENT_ID, PLAID_SECRET and a valid PLAID_ENV on the server to connect banks.")
    return client


async def _call[T](work: Awaitable[T]) -> T:
    try:
        return await work
    except expenses.PlaidNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except expenses.PlaidConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CredentialStorageUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except PlaidError as exc:
        raise HTTPException(502, str(exc)) from None


@router.get("/status")
def status(db: Session = Depends(get_db, scope="function")) -> expenses.PlaidStatus:
    return expenses.status(db, PlaidClient.configured())


@router.post("/link-token")
async def link_token(connection_id: str | None = None) -> PlaidLinkToken:
    client = _client()
    redirect_uri = client.redirect_uri() if current() else None
    token = await _call(expenses.link_token(client, redirect_uri, connection_id))
    return PlaidLinkToken(link_token=token, redirect_uri=redirect_uri)


@router.post("/exchange", status_code=201)
async def exchange(body: PlaidExchange) -> expenses.ConnectionStatus:
    return await _call(expenses.exchange(_client(), body.public_token, body.institution_name))


@router.post("/connections/{connection_id}/sync")
async def sync(connection_id: str) -> expenses.ConnectionStatus:
    return await _call(expenses.sync(_client(), connection_id))


@router.delete("/connections/{connection_id}", status_code=204)
async def disconnect(connection_id: str) -> Response:
    await _call(expenses.disconnect(PlaidClient.configured(), connection_id))
    return Response(status_code=204)
