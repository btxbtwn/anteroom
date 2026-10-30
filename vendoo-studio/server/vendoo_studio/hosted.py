"""Hosted API entry point. Seller data routes join after tenant isolation ships.

Unlike the desktop app, this starts no Chrome, Keychain or automation services.
"""

from __future__ import annotations

import os
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
            (scope["path"] == "/api/health" and scope["method"] == "GET")
            or (scope["method"] in {"GET", "HEAD"} and scope["app"].state.web_dir is not None
                and (scope["path"] == "/" or scope["path"].startswith("/assets/")))
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
            from vendoo_studio.services.hosted_context import for_seller
            from vendoo_studio.services.service_context import use
            with use(for_seller(identity.user_id, token)):
                await self.app(scope, receive, send)
            return
        await response(scope, receive, send)


class AccountResponse(BaseModel):
    user_id: str


def create_app(supabase_url: str | None = None, *, web_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ORM relationship names must resolve before a route constructs its
        # first query, which happens before the session's after_begin hook.
        from vendoo_studio.database import load_models
        from sqlalchemy.orm import configure_mappers
        load_models()
        configure_mappers()
        # Fail startup if the operator has not selected a project. No service
        # role key, shared signing secret or database password is needed here.
        app.state.session_verifier = SessionVerifier(
            supabase_url if supabase_url is not None else os.environ.get("SUPABASE_URL", ""),
        )
        app.state.chat_upload_slots = asyncio.Semaphore(1)
        worker = None
        if app.state.background_worker_enabled:
            from vendoo_studio.database import engine
            from vendoo_studio.services.background_worker import serve
            worker = asyncio.create_task(serve(engine))
        app.state.background_worker = worker
        try:
            yield
        finally:
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    app = FastAPI(
        title="Anteroom hosted API", version=app_version(), lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.state.web_dir = web_dir
    app.state.background_worker_enabled = os.environ.get('ANTEROOM_BACKGROUND_WORKER') == '1'
    app.add_middleware(SellerSessionMiddleware)

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self' https://*.supabase.co; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    from fastapi.exceptions import RequestValidationError
    from vendoo_studio.services.hosted_context import CredentialStorageUnavailable

    @app.exception_handler(CredentialStorageUnavailable)
    async def unavailable_credentials(_request, _error):
        return JSONResponse({'detail': 'Secure credential storage is temporarily unavailable'}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request, _error):
        # Pydantic's default response echoes rejected values, including secret
        # fields and unknown extra keys. The hosted boundary never reflects them.
        return JSONResponse({'detail': 'Check the supplied values and try again'}, status_code=422)

    from vendoo_studio.routes.hosted_drafts import router
    app.include_router(router)
    from vendoo_studio.routes.hosted_workspace import router as workspace_router
    app.include_router(workspace_router)
    from vendoo_studio.routes.hosted_photos import router as photo_router
    app.include_router(photo_router)
    from vendoo_studio.routes.hosted_settings import router as settings_router, keys_router
    app.include_router(settings_router)
    app.include_router(keys_router)
    from vendoo_studio.routes.hosted_tasks import router as task_router
    app.include_router(task_router)
    from vendoo_studio.routes.hosted_attachments import router as attachment_router
    app.include_router(attachment_router)
    from vendoo_studio.routes import analytics, boxes, sale_calendar, sales_ledger
    for shared_router in (analytics.router, boxes.router, sale_calendar.router, sales_ledger.router):
        app.include_router(shared_router)
    from vendoo_studio.routes.photos import edit_router
    app.include_router(edit_router)
    from vendoo_studio.routes import listing_templates, listing_copies
    app.include_router(listing_templates.router)
    app.include_router(listing_copies.router)
    from vendoo_studio.routes.conversations import preview_copy, copy_conversation, copy_result, ConversationResponse
    app.add_api_route('/api/conversations/{conv_id}/copy-preview', preview_copy, methods=['GET'])
    app.add_api_route('/api/conversations/{conv_id}/copy', copy_conversation, methods=['POST'], response_model=ConversationResponse)
    app.add_api_route('/api/conversations/{conv_id}/copy-result/{request_id}', copy_result, methods=['GET'], response_model=ConversationResponse)
    if web_dir is not None:
        app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="cloud-assets")

        @app.get("/", include_in_schema=False)
        def web_index():
            return FileResponse(web_dir / "cloud.html")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": app_version(), "service": "anteroom-hosted"}

    @app.get("/api/account", response_model=AccountResponse)
    def account(request: Request):
        return JSONResponse(
            {"user_id": request.state.seller.user_id}, headers={"Cache-Control": "no-store"},
        )

    return app


app = create_app(web_dir=Path(os.environ["ANTEROOM_WEB_DIR"]) if os.environ.get("ANTEROOM_WEB_DIR") else None)
