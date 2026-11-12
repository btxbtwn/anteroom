"""Hosted API entry point. Seller data routes join after tenant isolation ships.

Unlike the desktop app, this starts no Chrome, Keychain or automation services.
"""

from __future__ import annotations

import os
import asyncio
import logging
import threading
import time
import traceback
import uuid
from pathlib import Path
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import event
from starlette.datastructures import Headers, MutableHeaders
from vendoo_studio.services.request_timing import RequestTimingMiddleware

from vendoo_studio.services.seller_sessions import (
    InvalidSession,
    SessionVerificationUnavailable,
    SessionVerifier,
)
from vendoo_studio.version import app_version
from vendoo_studio.services.background_loop import BackgroundLoop, give_threads


def from_public_edge(scope) -> bool:
    """Whether Fly's edge proxy forwarded this request from the internet.

    The proxy adds ``Fly-Client-IP`` to every request it forwards, and a client
    cannot strip it. Machines call each other directly on the private network
    (``device_routes.local_base_url``), which never passes through that proxy.
    """
    return any(name == b"fly-client-ip" for name, _value in scope.get("headers") or ())


access_log = logging.getLogger("vendoo_studio.access")
http_log = logging.getLogger("vendoo_studio.http")


class AccessLogMiddleware:
    """One line per request: method, route template, status and duration.

    Uvicorn's own access log stays off because it prints the raw URL, and
    sign-in callbacks carry one-time codes in it. This never logs the path as
    sent, a query string, a header or a body.
    """

    def __init__(self, app):
        self.app = app
        if not access_log.handlers:
            # Hosted logging is otherwise unconfigured; INFO would be dropped.
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            access_log.addHandler(handler)
            access_log.setLevel(logging.INFO)
            access_log.propagate = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started, status, failed = time.perf_counter(), 0, False

        async def record(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        from vendoo_studio.services.error_alerts import request_context

        request_id = Headers(scope=scope).get("fly-request-id") or uuid.uuid4().hex
        details = {"scope": scope, "request_id": request_id}
        context = request_context.set(details)
        try:
            await self.app(scope, receive, record)
        except Exception:
            failed = True
            # The route template, never the raw path: it can carry one-time codes.
            http_log.exception("Unhandled exception in %s %s",
                               scope["method"], getattr(scope.get("route"), "path", None) or "-")
            raise
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "-"
            if status >= 500:
                http_log.error("%s %s answered %s", scope["method"], template, status,
                               exc_info=details.get("cause"))
            elif status >= 400:
                http_log.warning("%s %s answered %s", scope["method"], template, status)
            request_context.reset(context)
            # 499: the client closed the connection before any response started.
            access_log.info("%s %s %s %dms", scope["method"], template, status or (500 if failed else 499),
                            round((time.perf_counter() - started) * 1000))


class ResponseHeadersMiddleware:
    """Security and caching headers on every response.

    Plain ASGI rather than ``BaseHTTPMiddleware``: when a browser leaves before
    a response starts, nothing is sent, and that is not a server error.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # A route may let the browser keep a versioned private image; nothing else is kept.
                headers.setdefault("Cache-Control", "no-store")
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Content-Type-Options"] = "nosniff"
                # Browsers honor this only over HTTPS, so local HTTP sandboxes are unaffected.
                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                # pywebview builds its native API methods with Function(). WebKit
                # applies this policy to that bootstrap as well as to page scripts.
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob:; connect-src 'self' https://*.supabase.co wss://*.supabase.co; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                )
            await send(message)

        await self.app(scope, receive, with_headers)


class SellerSessionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            # No hosted WebSocket protocol exists yet. Do not accidentally
            # expose the desktop extension bridge through this entry point.
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] == "http" and scope["path"].startswith("/internal/") and from_public_edge(scope):
            # Process-to-process routes answer only on the private network.
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        if scope["type"] != "http" or (
            (scope["path"] in {"/api/health", "/api/ready", "/api/auth-config"} and scope["method"] == "GET")
            or scope["path"].startswith("/internal/")
            or (scope["method"] in {"GET", "HEAD"} and scope["app"].state.web_dir is not None
                and (scope["path"] in {"/", "/favicon.png", "/privacy"} or scope["path"].startswith("/assets/")))
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
            # JWKS retrieval is synchronous. Keep it off the event loop and
            # the request thread limiter: slow inventory endpoints must not
            # prevent the Mac from authenticating its heartbeats and replies.
            identity = await asyncio.to_thread(verifier.verify, token)
        except InvalidSession:
            response = JSONResponse(
                {"detail": "A valid seller session is required"}, status_code=401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
        except SessionVerificationUnavailable as exc:
            from vendoo_studio.services.error_alerts import note_cause

            note_cause(exc.__cause__ or exc)
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


class LoopCheckoutWarning:
    """Log each place that takes a pooled connection on the request loop.

    That checkout blocks every request while it waits, so each one found here
    belongs in a worker thread.
    """

    def __init__(self, engine):
        self.engine, self.loop_thread, self.seen = engine, threading.get_ident(), set()
        event.listen(engine, "checkout", self.checkout)

    def checkout(self, *_args):
        if threading.get_ident() != self.loop_thread:
            return
        frames = [frame for frame in traceback.extract_stack() if "/vendoo_studio/" in frame.filename]
        site = " < ".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in reversed(frames[-4:]))
        if site not in self.seen:
            self.seen.add(site)
            logging.getLogger(__name__).warning("Database connection taken on the request event loop: %s", site)

    def close(self):
        event.remove(self.engine, "checkout", self.checkout)


class AccountResponse(BaseModel):
    user_id: str


def create_app(supabase_url: str | None = None, *, web_dir: Path | None = None, session_verifier: SessionVerifier | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        give_threads(asyncio.get_running_loop())
        from vendoo_studio.services.error_alerts import install_alerts, uninstall_alerts
        install_alerts()
        # ORM relationship names must resolve before a route constructs its
        # first query, which happens before the session's after_begin hook.
        from vendoo_studio.database import load_models
        from sqlalchemy.orm import configure_mappers
        load_models()
        configure_mappers()
        # Fail startup if the operator has not selected a project. No service
        # role key, shared signing secret or database password is needed here.
        app.state.session_verifier = session_verifier or SessionVerifier(
            supabase_url if supabase_url is not None else os.environ.get("SUPABASE_URL", ""),
        )
        from vendoo_studio.services.hosted_limits import SellerRateLimit, UploadSlots
        app.state.upload_slots = UploadSlots()
        app.state.submission_limit = SellerRateLimit()
        from vendoo_studio.database import engine as database_engine
        loop_checkouts = LoopCheckoutWarning(database_engine)
        from vendoo_studio.services import chrome_wait_connections
        watching_chrome_waits = chrome_wait_connections.watch(database_engine)
        from vendoo_studio.services import process_handoff
        process_handoff.reset()
        from vendoo_studio.services import task_signals
        if app.state.process_role != 'worker' and database_engine.dialect.name == 'postgresql':
            # Open draft and assistant streams wait for Postgres to announce new progress.
            task_signals.start(database_engine.url)
        worker = None
        # Lease name -> what starts that set of schedulers (process_handoff.lead).
        starts: dict[str, Callable[[], Callable[[], None]]] = {}
        stopping = threading.Event()
        backgrounds = []

        def start_background(coroutine, name):
            background = BackgroundLoop(name)
            backgrounds.append(background)
            return background, background.run(coroutine)

        def scheduler_set(jobs):
            # Each service opens blocking database sessions. An offer or
            # watchdog read must not stop draft lease renewal, another service
            # or the request loop while it waits for the database.
            def start():
                loops = []
                for serve, name in jobs:
                    loop = BackgroundLoop(name)
                    loop.run(serve())
                    loops.append(loop)

                def stop():
                    threads = [threading.Thread(target=loop.stop) for loop in loops]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()
                return stop
            return start

        if app.state.background_worker_enabled:
            from vendoo_studio.database import engine
            from vendoo_studio.services import (
                background_worker, hosted_automation_scheduler, hosted_job_watchdog, hosted_offer_scheduler,
                hosted_sourcing_scheduler, hosted_sales_scheduler,
            )
            from vendoo_studio.services import background_tasks as queue
            from vendoo_studio.routes.assistant import answer_task
            # Assistant answers share the draft worker; the route supplies how to answer one.
            background_worker.register(queue.ASSISTANT, answer_task)
            # Drafts are claimed row by row, so every Machine may run this worker.
            _, worker = start_background(background_worker.serve(engine, stopping), 'anteroom-drafts')
            starts[process_handoff.LEASE] = scheduler_set([
                # Automations still submit regenerations through the same durable queue.
                (lambda: hosted_automation_scheduler.serve(engine), 'anteroom-automations'),
                (lambda: hosted_offer_scheduler.serve(engine), 'anteroom-offers'),
                # Frees the marketplace slot a restart left held and reads unconfirmed results.
                (lambda: hosted_job_watchdog.serve(engine), 'anteroom-watchdog'),
                # Refreshes each seller's due buy list, as the Mac's Sourcing timer does.
                (lambda: hosted_sourcing_scheduler.serve(engine), 'anteroom-sourcing'),
                (lambda: hosted_sales_scheduler.serve(engine), 'anteroom-sales'),
            ])
        if os.environ.get('ANTEROOM_BACKGROUND_WORKER') == '1' and app.state.process_role != 'worker':
            from vendoo_studio.services import hosted_promotion_scheduler, hosted_sale_checks
            # These act in sellers' Chrome tabs, which connect to the web
            # Machine; only that process knows which tabs are connected.
            starts[process_handoff.CHROME_LEASE] = scheduler_set([
                (hosted_sale_checks.serve, 'anteroom-chrome-sales'),
                (hosted_promotion_scheduler.serve, 'anteroom-promotions'),
            ])

        from vendoo_studio.database import engine as handoff_engine
        # Hands the schedulers, Mac and Chrome to the next Machine when this one stops.
        _, handoff = start_background(
            process_handoff.lead(handoff_engine, starts, stopping.set), 'anteroom-handoff',
        )
        app.state.background_worker = worker
        app.state.handoff = handoff
        try:
            yield
        finally:
            loop_checkouts.close()
            if watching_chrome_waits:
                chrome_wait_connections.unwatch(database_engine)
            process_handoff.begin_draining()
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(handoff)), timeout=30)
            except Exception:
                logging.getLogger(__name__).warning('Scheduled work did not stop before shutdown')
            stopping.set()
            try:
                if worker is not None:
                    # A disconnected browser does not mean its draft is done.
                    # Let the current task save before cancelling the background loop.
                    await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(worker)), timeout=120)
            except TimeoutError:
                logging.getLogger(__name__).warning('Draft worker exceeded the shutdown grace period')
            except Exception:
                logging.getLogger(__name__).warning('Draft worker stopped before shutdown completed')
            finally:
                await asyncio.gather(*(asyncio.to_thread(background.stop) for background in backgrounds))
                await asyncio.to_thread(task_signals.stop)
                await asyncio.to_thread(uninstall_alerts)

    app = FastAPI(
        title="Anteroom hosted API", version=app_version(), lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.state.web_dir = web_dir
    app.state.process_role = os.environ.get('ANTEROOM_PROCESS_ROLE', 'all')
    # On Fly, `web` Machines serve sellers and device connections, and `worker`
    # Machines run drafts and the schedulers (deploy/fly/fly.toml). The Mac, the
    # sandbox and staging run both in one process (`all`).
    app.state.background_worker_enabled = (
        os.environ.get('ANTEROOM_BACKGROUND_WORKER') == '1' and app.state.process_role != 'web'
    )
    from vendoo_studio.routes.error_reports import router as error_reports_router
    app.include_router(error_reports_router)
    app.add_middleware(SellerSessionMiddleware)
    # JSON forms and choice lists (Depop's brands are about 300 KB) shrink several
    # times over a phone connection. Event streams and images are left alone.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    app.add_middleware(ResponseHeadersMiddleware)

    # Outermost, so a refused session or an error still gets its line.
    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(AccessLogMiddleware)

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

    from vendoo_studio.services.hosted_read_snapshots import SellerReadSnapshots
    app.state.seller_reads = SellerReadSnapshots()
    from vendoo_studio.routes.hosted_drafts import router
    app.include_router(router)
    from vendoo_studio.routes.internal_devices import router as internal_router
    app.include_router(internal_router)
    from vendoo_studio.routes.hosted_workspace import router as workspace_router
    app.include_router(workspace_router)
    from vendoo_studio.routes.vendoo_drafts import router as vendoo_drafts_router
    app.include_router(vendoo_drafts_router)
    from vendoo_studio.routes.hosted_photos import router as photo_router
    app.include_router(photo_router)
    from vendoo_studio.routes.hosted_settings import router as settings_router, keys_router
    app.include_router(settings_router)
    app.include_router(keys_router)
    from vendoo_studio.routes.hosted_tasks import router as task_router
    app.include_router(task_router)
    from vendoo_studio.routes.mac_inference import router as mac_ai_router
    app.include_router(mac_ai_router)
    from vendoo_studio.routes.hosted_attachments import router as attachment_router
    app.include_router(attachment_router)
    from vendoo_studio.routes import analytics, boxes, sale_calendar, sales_ledger
    from vendoo_studio.routes import scout, sourcing
    for shared_router in (analytics.router, boxes.router, sale_calendar.router, sales_ledger.router, sourcing.router, scout.router):
        app.include_router(shared_router)
    from vendoo_studio.routes.photos import edit_router
    app.include_router(edit_router)
    # Bulk listing reads photos not uploaded yet; a saved listing reads its photos from private storage.
    from vendoo_studio.routes import measurements
    app.add_api_route('/api/measurements/read', measurements.read_measurements, methods=['POST'], tags=['measurements'])
    app.add_api_route('/api/conversations/{conv_id}/measurements/read', measurements.read_hosted_saved_measurements,
                      methods=['POST'], tags=['measurements'])
    from vendoo_studio.routes import listing_templates, listing_copies, similar_items, changelog
    app.include_router(listing_templates.router)
    app.include_router(listing_copies.router)
    app.include_router(similar_items.router)
    app.include_router(changelog.router)
    from vendoo_studio.routes.hosted_prices import router as prices_router
    app.include_router(prices_router)
    from vendoo_studio.routes.hosted_marketplace_accounts import router as accounts_router
    app.include_router(accounts_router)
    from vendoo_studio.routes.hosted_marketplaces import router as marketplaces_router
    app.include_router(marketplaces_router)
    from vendoo_studio.routes.hosted_direct_listing import router as direct_listing_router
    app.include_router(direct_listing_router)
    from vendoo_studio.routes import listing_readiness as listing_readiness_route
    app.include_router(listing_readiness_route.router)
    from vendoo_studio.routes.hosted_browser_relay import router as browser_relay_router, sessions_router
    app.include_router(browser_relay_router)
    app.include_router(sessions_router)
    from vendoo_studio.routes import listing_promotions, promotion_automations
    app.include_router(listing_promotions.router)
    app.include_router(promotion_automations.router)
    # Chrome inventory import: reads in the seller's own Chrome, through their Anteroom tab.
    from vendoo_studio.routes import chrome_import
    app.include_router(chrome_import.router)
    from vendoo_studio.routes import assistant
    app.include_router(assistant.router)
    from vendoo_studio.routes.hosted_automations import errors_router, router as automations_router
    app.include_router(automations_router)
    app.include_router(errors_router)
    from vendoo_studio.routes.hosted_bulk_edit import router as bulk_edit_router
    app.include_router(bulk_edit_router)
    from vendoo_studio.routes.hosted_account_import import router as account_import_router
    app.include_router(account_import_router)
    from vendoo_studio.routes.hosted_bulk_delete import router as bulk_delete_router
    app.include_router(bulk_delete_router)
    from vendoo_studio.routes.hosted_account import router as account_router
    app.include_router(account_router)
    from vendoo_studio.routes.listings import apply_price_drop, preview_price_drop, stream_price_drop_comps
    app.add_api_route('/api/conversations/{conv_id}/price-drop/preview', preview_price_drop, methods=['POST'])
    app.add_api_route('/api/conversations/{conv_id}/price-drop/comps', stream_price_drop_comps, methods=['POST'])
    app.add_api_route('/api/conversations/{conv_id}/price-drop', apply_price_drop, methods=['POST'])
    from vendoo_studio.routes.catalog import categories as catalog_categories, choice_list, dropdown_options, search as catalog_search
    app.add_api_route('/api/catalog/choice-lists/{name}', choice_list, methods=['GET'])
    app.add_api_route('/api/catalog/dropdown-options', dropdown_options, methods=['GET'])
    app.add_api_route('/api/catalog/categories', catalog_categories, methods=['GET'])
    app.add_api_route('/api/catalog/search', catalog_search, methods=['GET'])
    from vendoo_studio.routes.conversations import preview_copy, copy_conversation, copy_result, Conversation
    app.add_api_route('/api/conversations/{conv_id}/copy-preview', preview_copy, methods=['GET'])
    app.add_api_route('/api/conversations/{conv_id}/copy', copy_conversation, methods=['POST'], response_model=Conversation)
    app.add_api_route('/api/conversations/{conv_id}/copy-result/{request_id}', copy_result, methods=['GET'], response_model=Conversation)
    if web_dir is not None:
        app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="cloud-assets")

        @app.get("/", include_in_schema=False)
        def web_index():
            return FileResponse(web_dir / "cloud.html")

        @app.get("/favicon.png", include_in_schema=False)
        def web_favicon():
            return FileResponse(web_dir / "favicon.png", media_type="image/png")

        @app.get("/privacy", include_in_schema=False)
        def web_privacy():
            # Linked from the Chrome Web Store listing; reviewers have no session.
            return FileResponse(web_dir / "privacy.html", media_type="text/html")

    @app.get("/api/auth-config")
    def auth_config():
        # Public browser configuration (also in the web bundle): lets the Mac
        # app sign in to the same project when it moves its listings.
        return {"supabase_url": os.environ.get("SUPABASE_URL", ""),
                "publishable_key": os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")}

    @app.get("/api/health")
    def health():
        from vendoo_studio.services import task_signals
        worker = getattr(app.state, "background_worker", None)
        running = bool(app.state.background_worker_enabled and worker is not None and not worker.done())
        body = {
            "status": "ok", "version": app_version(), "service": "anteroom-hosted",
            "role": app.state.process_role, "background_worker": running,
            # Whether open streams hear about new progress, or read on a timer meanwhile.
            "progress_listener": task_signals.health(),
        }
        if app.state.background_worker_enabled and worker is not None and worker.done():
            # The draft loop died: fail the check so Fly restarts this Machine
            # instead of leaving queued drafts waiting on it.
            return JSONResponse({**body, "status": "worker_stopped"}, status_code=503)
        return body

    # Reading the revision scripts takes a while; they never change in a running build.
    from functools import cache
    from vendoo_studio.services.schema_migrations import head_revision
    built_revision = cache(head_revision)

    @app.get("/api/ready")
    def ready():
        """Whether this process can reach Postgres at the schema it was built for.

        Not the Fly routing check: with one machine, a database blip must not
        stop the proxy from routing anywhere. ``/api/health`` stays the
        liveness check until a second machine can take traffic. The deploy
        workflow checks this after the new version is live.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        from vendoo_studio.database import engine
        if engine.dialect.name != "postgresql":
            return {"status": "ok"}
        try:
            with engine.connect() as connection, connection.begin():
                # LOCAL so a reused connection does not keep the one-second limit.
                connection.execute(text("SET LOCAL statement_timeout = '1000'"))
                revision = connection.scalar(text("SELECT anteroom_private.schema_revision()"))
        except SQLAlchemyError as exc:
            from vendoo_studio.services.error_alerts import note_cause
            note_cause(exc)
            return JSONResponse({"status": "unavailable"}, status_code=503, headers={"Cache-Control": "no-store"})
        if revision != built_revision():
            # Revisions are not echoed: this route needs no session.
            return JSONResponse({"status": "schema_mismatch"}, status_code=503, headers={"Cache-Control": "no-store"})
        return {"status": "ok"}

    @app.get("/api/account", response_model=AccountResponse)
    def account(request: Request):
        return JSONResponse(
            {"user_id": request.state.seller.user_id}, headers={"Cache-Control": "no-store"},
        )

    return app


app = create_app(web_dir=Path(os.environ["ANTEROOM_WEB_DIR"]) if os.environ.get("ANTEROOM_WEB_DIR") else None)
