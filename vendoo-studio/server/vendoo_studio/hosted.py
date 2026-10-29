"""Hosted API entry point. Seller data routes join after tenant isolation ships.

Unlike the desktop app, this starts no Chrome, Keychain or automation services.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers

from vendoo_studio.services.seller_sessions import (
    InvalidSession,
    SessionVerificationUnavailable,
    SessionVerifier,
)
from vendoo_studio.version import app_version


class SellerSessionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            # No hosted WebSocket protocol exists yet. Do not accidentally
            # expose the desktop extension bridge through this entry point.
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http" or (
            scope["path"] == "/api/health" and scope["method"] == "GET"
        ):
            await self.app(scope, receive, send)
            return
        try:
            values = Headers(scope=scope).getlist("authorization")
            if len(values) != 1:
                raise InvalidSession
            scheme, _, token = values[0].partition(" ")
            if scheme.lower() != "bearer" or not token or len(token) > 16384:
                raise InvalidSession
            verifier = scope["app"].state.session_verifier
            # JWKS retrieval is synchronous; never block streaming requests
            # or other sellers while the public-key endpoint responds.
            identity = await run_in_threadpool(verifier.verify, token)
        except InvalidSession:
            response = JSONResponse(
                {"detail": "A valid seller session is required"}, status_code=401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
        except SessionVerificationUnavailable:
            response = JSONResponse(
                {"detail": "Session verification is temporarily unavailable"}, status_code=503,
                headers={"Retry-After": "5", "Cache-Control": "no-store"},
            )
        else:
            scope.setdefault("state", {})["seller"] = identity
            await self.app(scope, receive, send)
            return
        await response(scope, receive, send)


class AccountResponse(BaseModel):
    user_id: str


def create_app(supabase_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Fail startup if the operator has not selected a project. No service
        # role key, shared signing secret or database password is needed here.
        app.state.session_verifier = SessionVerifier(
            supabase_url if supabase_url is not None else os.environ.get("SUPABASE_URL", ""),
        )
        yield

    app = FastAPI(
        title="Anteroom hosted API", version=app_version(), lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.add_middleware(SellerSessionMiddleware)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": app_version(), "service": "anteroom-hosted"}

    @app.get("/api/account", response_model=AccountResponse)
    def account(request: Request):
        return JSONResponse(
            {"user_id": request.state.seller.user_id}, headers={"Cache-Control": "no-store"},
        )

    return app


app = create_app()
