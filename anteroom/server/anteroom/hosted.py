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
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import event
from starlette.datastructures import Headers, MutableHeaders
from anteroom import app_module_path
from anteroom.services.request_timing import RequestTimingMiddleware

from anteroom.services.seller_sessions import (
    InvalidSession,
    SessionVerificationUnavailable,
    SessionVerifier,
)
from anteroom.version import app_version
from anteroom.services.background_loop import BackgroundLoop, give_threads


def from_public_edge(scope) -> bool:
    """Whether Fly's edge proxy forwarded this request from the internet.

    The proxy adds ``Fly-Client-IP`` to every request it forwards, and a client
    cannot strip it. Machines call each other directly on the private network
    (``device_routes.local_base_url``), which never passes through that proxy.
    """
    return any(name == b"fly-client-ip" for name, _value in scope.get("headers") or ())


access_log = logging.getLogger("anteroom.access")
http_log = logging.getLogger("anteroom.http")

# Session verification's own threads. A check is a signature check, and at
# most a JWKS read with a 5 s timeout once the cached key set expires, so a
# few threads keep up with every request the Machine serves.
AUTH_THREADS = 4

_on_worker_stopped: Callable[[BaseException | None], None] | None = None


def set_worker_stopped_handler(handler: Callable[[BaseException | None], None] | None) -> None:
    """Register the server callback invoked if the draft worker stops outside shutdown."""
    global _on_worker_stopped
    _on_worker_stopped = handler


def _skip_client_warning(scope, route, status: int, *, fly_replay: bool) -> bool:
    """Client/routing 4xx responses that are routine and should not alert the operator."""
    if fly_replay and status == 409:
        return True
    if route is None and status in {401, 404, 405}:
        return True
    if scope["method"] == "HEAD" and status == 405:
        return True
    if getattr(route, "path", None) == "/api/marketplaces/sessions" and status == 409:
        return True
    return False


class AccessLogMiddleware:
    """One line per request: method, route template, status and duration.

    Uvicorn's own access log stays off because it prints the raw URL, and
    sign-in callbacks carry one-time codes in it. This never logs the path as
    sent, a query string, a header or a body.
    """

    def __init__(self, app):
        self.app = app
        # Hosted logging is otherwise unconfigured; INFO would be dropped. Set
        # the level even when a handler is already attached, or that handler
        # receives nothing.
        if access_log.level == logging.NOTSET:
            access_log.setLevel(logging.INFO)
        if not access_log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            access_log.addHandler(handler)
            access_log.propagate = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started, status, failed, fly_replay = time.perf_counter(), 0, False, False

        async def record(message):
            nonlocal status, fly_replay
            if message["type"] == "http.response.start":
                status = message["status"]
                fly_replay = any(name.lower() == b"fly-replay" for name, _ in message.get("headers") or ())
            await send(message)

        from anteroom.services.error_alerts import request_context

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
            elif status >= 400 and not _skip_client_warning(scope, route, status, fly_replay=fly_replay):
                http_log.warning("%s %s answered %s", scope["method"], template, status)
            request_context.reset(context)
            # 499: the client closed the connection before any response started.
            access_log.info("%s %s %s %dms", scope["method"], template, status or (500 if failed else 499),
                            round((time.perf_counter() - started) * 1000))


def _build_csp(supabase_url: str) -> str:
    """Build the hosted CSP, pinning connect-src/img-src to the configured Supabase origin."""
    parsed = urlparse((supabase_url or "").strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        http_origin = f"{parsed.scheme}://{parsed.netloc}"
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        ws_origin = f"{ws_scheme}://{parsed.netloc}"
        img_src = f"'self' data: blob: {http_origin}"
        connect_src = f"'self' {http_origin} {ws_origin}"
    else:
        img_src = "'self' data: blob:"
        connect_src = "'self' https://*.supabase.co wss://*.supabase.co"
    # pywebview builds its native API methods with Function(). WebKit
    # applies this policy to that bootstrap as well as to page scripts.
    return (
        "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        f"img-src {img_src}; connect-src {connect_src}; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )


class ResponseHeadersMiddleware:
    """Security and caching headers on every response.

    Plain ASGI rather than ``BaseHTTPMiddleware``: when a browser leaves before
    a response starts, nothing is sent, and that is not a server error.
    """

    def __init__(self, app, supabase_url: str | None = None):
        self.app = app
        self._explicit_supabase_url = supabase_url

    def _csp(self) -> str:
        url = self._explicit_supabase_url if self._explicit_supabase_url is not None else os.environ.get("SUPABASE_URL", "")
        return _build_csp(url)

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
                headers["Content-Security-Policy"] = self._csp()
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
            or (
                scope["method"] == "POST"
                and scope["path"].startswith("/api/cloud-sessions/")
                and scope["path"].endswith("/upload")
                and scope["path"].count("/") == 4
            )
            or scope["path"].startswith("/internal/")
            or (scope["method"] in {"GET", "HEAD"} and scope["app"].state.web_dir is not None
                and (scope["path"] in {"/", "/favicon.png", "/privacy", "/terms", "/robots.txt"} or scope["path"].startswith("/assets/")))
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
            state = scope["app"].state
            # JWKS retrieval is synchronous. Keep it off the event loop, the
            # request thread limiter and the loop's shared pool: database steps
            # waiting for a connection and photo copies to Storage fill that
            # pool, and must not stop the Mac and Chrome authenticating their
            # heartbeats and replies.
            identity = await asyncio.get_running_loop().run_in_executor(
                state.auth_threads, state.session_verifier.verify, token)
        except InvalidSession:
            response = JSONResponse(
                {"detail": "A valid seller session is required"}, status_code=401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
        except SessionVerificationUnavailable as exc:
            from anteroom.services.error_alerts import note_cause

            note_cause(exc.__cause__ or exc)
            response = JSONResponse(
                {"detail": "Session verification is temporarily unavailable"}, status_code=503,
                headers={"Retry-After": "5", "Cache-Control": "no-store"},
            )
        else:
            scope.setdefault("state", {})["seller"] = identity
            from anteroom.services.hosted_context import for_seller
            from anteroom.services.service_context import use
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
        frames = [frame for frame in traceback.extract_stack() if app_module_path(frame.filename)]
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
        from anteroom.services import shared_http
        from anteroom.services.error_alerts import install_alerts, uninstall_alerts
        install_alerts()
        from anteroom.services.error_log import install_assistant_timings
        install_assistant_timings()
        # ORM relationship names must resolve before a route constructs its
        # first query, which happens before the session's after_begin hook.
        from anteroom.database import load_models
        from sqlalchemy.orm import configure_mappers
        load_models()
        configure_mappers()
        # Fail startup if the operator has not selected a project. No service
        # role key, shared signing secret or database password is needed here.
        app.state.session_verifier = session_verifier or SessionVerifier(
            supabase_url if supabase_url is not None else os.environ.get("SUPABASE_URL", ""),
        )
        from anteroom.services.hosted_limits import (
            ERROR_REPORT_BURST,
            ERROR_REPORT_PER_SECOND,
            SellerRateLimit,
            UploadSlots,
        )
        app.state.upload_slots = UploadSlots()
        app.state.submission_limit = SellerRateLimit()
        app.state.error_report_limit = SellerRateLimit(
            burst=ERROR_REPORT_BURST, per_second=ERROR_REPORT_PER_SECOND,
        )
        from anteroom.database import engine as database_engine
        loop_checkouts = LoopCheckoutWarning(database_engine)
        from anteroom.services import chrome_wait_connections
        watching_chrome_waits = chrome_wait_connections.watch(database_engine)
        from anteroom.services import process_handoff
        process_handoff.reset()
        from anteroom.services import task_signals
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
            from anteroom.database import engine
            from anteroom.services import (
                background_worker, hosted_automation_scheduler, hosted_job_watchdog, hosted_offer_scheduler,
                hosted_promotion_scheduler, hosted_sale_checks, hosted_sourcing_scheduler, hosted_sales_scheduler,
            )
            from anteroom.services import background_tasks as queue
            from anteroom.routes.assistant import answer_task
            # Assistant answers share the draft worker; the route supplies how to answer one.
            background_worker.register(queue.ASSISTANT, answer_task)
            # Drafts are claimed row by row, so every Machine may run this worker.
            _, worker = start_background(background_worker.serve(engine, stopping), 'anteroom-drafts')

            def _watch_worker(done_future) -> None:
                if stopping.is_set() or process_handoff.draining():
                    return
                exc: BaseException | None = None
                if not done_future.cancelled():
                    exc = done_future.exception()
                logging.getLogger(__name__).critical(
                    'Background draft worker stopped outside shutdown; requesting process exit so Fly restarts this Machine',
                    exc_info=exc,
                )
                handler = _on_worker_stopped
                if handler is not None:
                    handler(exc)

            worker.add_done_callback(_watch_worker)
            starts[process_handoff.LEASE] = scheduler_set([
                # Automations still submit regenerations through the same durable queue.
                (lambda: hosted_automation_scheduler.serve(engine), 'anteroom-automations'),
                (lambda: hosted_offer_scheduler.serve(engine), 'anteroom-offers'),
                # Frees the marketplace slot a restart left held and reads unconfirmed results.
                (lambda: hosted_job_watchdog.serve(engine), 'anteroom-watchdog'),
                # Refreshes each seller's due buy list, as the Mac's Sourcing timer does.
                (lambda: hosted_sourcing_scheduler.serve(engine), 'anteroom-sourcing'),
                (lambda: hosted_sales_scheduler.serve(engine), 'anteroom-sales'),
                # Cloud marketplace sales and promotion schedulers run only on worker/all Machines.
                (lambda: hosted_sale_checks.serve_cloud(engine), 'anteroom-cloud-sales'),
                (lambda: hosted_promotion_scheduler.serve_cloud(engine), 'anteroom-cloud-promotions'),
            ])
        if os.environ.get('ANTEROOM_BACKGROUND_WORKER') == '1' and app.state.process_role != 'worker':
            from anteroom.services import hosted_promotion_scheduler, hosted_sale_checks
            # These act in sellers' Chrome tabs, which connect to the web
            # Machine; only that process knows which tabs are connected.
            starts[process_handoff.CHROME_LEASE] = scheduler_set([
                (hosted_sale_checks.serve, 'anteroom-chrome-sales'),
                (hosted_promotion_scheduler.serve, 'anteroom-promotions'),
            ])

        from anteroom.database import engine as handoff_engine
        # Hands the schedulers, Mac and Chrome to the next Machine when this one stops.
        _, handoff = start_background(
            process_handoff.lead(handoff_engine, starts, stopping.set), 'anteroom-handoff',
        )
        app.state.background_worker = worker
        app.state.handoff = handoff
        try:
            shared_http.keep_connection_on_this_loop()
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
                await shared_http.close_connection()

    app = FastAPI(
        title="Anteroom hosted API", version=app_version(), lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.state.web_dir = web_dir
    # Threads start on first use and idle between requests; they need no shutdown.
    app.state.auth_threads = ThreadPoolExecutor(max_workers=AUTH_THREADS, thread_name_prefix="anteroom-auth")
    app.state.process_role = os.environ.get('ANTEROOM_PROCESS_ROLE', 'all')
    # On Fly, `web` Machines serve sellers and device connections, and `worker`
    # Machines run drafts and the schedulers (deploy/fly/fly.toml). The Mac, the
    # sandbox and staging run both in one process (`all`).
    app.state.background_worker_enabled = (
        os.environ.get('ANTEROOM_BACKGROUND_WORKER') == '1' and app.state.process_role != 'web'
    )
    from anteroom.routes.error_reports import router as error_reports_router
    app.include_router(error_reports_router)
    app.add_middleware(SellerSessionMiddleware)
    # JSON forms and choice lists (Depop's brands are about 300 KB) shrink several
    # times over a phone connection. Event streams and images are left alone.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    app.add_middleware(ResponseHeadersMiddleware, supabase_url=supabase_url)

    # Outermost, so a refused session or an error still gets its line.
    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(AccessLogMiddleware)

    from fastapi.exceptions import RequestValidationError
    from sqlalchemy.exc import SQLAlchemyError
    from anteroom.services.hosted_context import CredentialStorageUnavailable

    @app.exception_handler(CredentialStorageUnavailable)
    async def unavailable_credentials(_request, _error):
        return JSONResponse({'detail': 'Secure credential storage is temporarily unavailable'}, status_code=503)

    @app.exception_handler(SQLAlchemyError)
    async def unavailable_database(_request, error):
        from anteroom.services.error_alerts import note_cause

        note_cause(error)
        return JSONResponse(
            {'detail': 'Cloud data is temporarily unavailable'},
            status_code=503,
            headers={'Retry-After': '5', 'Cache-Control': 'no-store'},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request, _error):
        # Pydantic's default response echoes rejected values, including secret
        # fields and unknown extra keys. The hosted boundary never reflects them.
        return JSONResponse({'detail': 'Check the supplied values and try again'}, status_code=422)

    from anteroom.services.hosted_read_snapshots import SellerReadSnapshots
    app.state.seller_reads = SellerReadSnapshots()
    from anteroom.routes.hosted_drafts import router
    app.include_router(router)
    from anteroom.routes.internal_devices import router as internal_router
    app.include_router(internal_router)
    from anteroom.routes.hosted_workspace import router as workspace_router
    app.include_router(workspace_router)
    from anteroom.routes.vendoo_drafts import router as vendoo_drafts_router
    app.include_router(vendoo_drafts_router)
    from anteroom.routes.hosted_photos import router as photo_router
    app.include_router(photo_router)
    from anteroom.routes.hosted_settings import router as settings_router, keys_router
    app.include_router(settings_router)
    app.include_router(keys_router)
    from anteroom.routes.hosted_tasks import router as task_router
    app.include_router(task_router)
    from anteroom.routes.mac_inference import router as mac_ai_router
    app.include_router(mac_ai_router)
    from anteroom.routes.hosted_attachments import router as attachment_router
    app.include_router(attachment_router)
    from anteroom.routes import analytics, boxes, sale_calendar, sales_ledger
    from anteroom.routes import business_expenses, scout, sourcing
    for shared_router in (analytics.router, boxes.router, sale_calendar.router, sales_ledger.router, sourcing.router, scout.router,
                          business_expenses.router):
        app.include_router(shared_router)
    from anteroom.routes.photos import edit_router
    app.include_router(edit_router)
    # Bulk listing reads photos not uploaded yet; a saved listing reads its photos from private storage.
    from anteroom.routes import measurements
    app.include_router(measurements.hosted_router)
    from anteroom.routes import listing_templates, listing_copies, similar_items, changelog
    app.include_router(listing_templates.router)
    app.include_router(listing_copies.router)
    app.include_router(similar_items.router)
    app.include_router(changelog.router)
    from anteroom.routes.hosted_prices import router as prices_router
    app.include_router(prices_router)
    from anteroom.routes.hosted_marketplace_accounts import router as accounts_router
    app.include_router(accounts_router)
    from anteroom.routes.hosted_marketplaces import router as marketplaces_router
    app.include_router(marketplaces_router)
    from anteroom.routes.hosted_direct_listing import router as direct_listing_router
    app.include_router(direct_listing_router)
    from anteroom.routes import listing_readiness as listing_readiness_route
    app.include_router(listing_readiness_route.router)
    from anteroom.routes import listing_sheet as listing_sheet_route
    app.include_router(listing_sheet_route.router)
    from anteroom.routes.hosted_browser_relay import router as browser_relay_router, sessions_router
    app.include_router(browser_relay_router)
    app.include_router(sessions_router)
    from anteroom.routes.cloud_sessions import router as cloud_sessions_router
    app.include_router(cloud_sessions_router)
    from anteroom.routes import listing_promotions, promotion_automations
    app.include_router(listing_promotions.router)
    app.include_router(promotion_automations.router)
    # Chrome inventory import: reads in the seller's own Chrome, through their Anteroom tab.
    from anteroom.routes import chrome_import
    app.include_router(chrome_import.router)
    from anteroom.routes import assistant
    app.include_router(assistant.router)
    from anteroom.routes.hosted_automations import errors_router, router as automations_router
    app.include_router(automations_router)
    app.include_router(errors_router)
    from anteroom.routes.hosted_bulk_edit import router as bulk_edit_router
    app.include_router(bulk_edit_router)
    from anteroom.routes.hosted_account_import import router as account_import_router
    app.include_router(account_import_router)
    from anteroom.routes.hosted_bulk_delete import router as bulk_delete_router
    app.include_router(bulk_delete_router)
    from anteroom.routes.hosted_account import router as account_router
    app.include_router(account_router)
    from anteroom.routes.listings import apply_price_drop, preview_price_drop, stream_price_drop_comps
    from anteroom.services.hosted_limits import limit_seller_submissions
    app.add_api_route('/api/conversations/{conv_id}/price-drop/preview', preview_price_drop, methods=['POST'])
    app.add_api_route(
        '/api/conversations/{conv_id}/price-drop/comps',
        stream_price_drop_comps,
        methods=['POST'],
        dependencies=[Depends(limit_seller_submissions)],
    )
    app.add_api_route('/api/conversations/{conv_id}/price-drop', apply_price_drop, methods=['POST'])
    from anteroom.routes.catalog import categories as catalog_categories, choice_list, dropdown_options, search as catalog_search
    app.add_api_route('/api/catalog/choice-lists/{name}', choice_list, methods=['GET'])
    app.add_api_route('/api/catalog/dropdown-options', dropdown_options, methods=['GET'])
    app.add_api_route('/api/catalog/categories', catalog_categories, methods=['GET'])
    app.add_api_route('/api/catalog/search', catalog_search, methods=['GET'])
    from anteroom.routes.conversations import preview_copy, copy_conversation, copy_result, Conversation
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

        @app.get("/terms", include_in_schema=False)
        def web_terms():
            return FileResponse(web_dir / "terms.html", media_type="text/html")

        @app.get("/robots.txt", include_in_schema=False)
        def web_robots():
            return FileResponse(web_dir / "robots.txt", media_type="text/plain")

    @app.get("/api/auth-config")
    def auth_config():
        # Public browser configuration (also in the web bundle): lets the Mac
        # app sign in to the same project when it moves its listings.
        return {"supabase_url": os.environ.get("SUPABASE_URL", ""),
                "publishable_key": os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")}

    @app.get("/api/health")
    def health():
        from anteroom.services import task_signals
        worker = getattr(app.state, "background_worker", None)
        running = bool(app.state.background_worker_enabled and worker is not None and not worker.done())
        body = {
            "status": "ok", "version": app_version(), "service": "anteroom-hosted",
            "role": app.state.process_role, "background_worker": running,
            # Whether open streams hear about new progress, or read on a timer meanwhile.
            "progress_listener": task_signals.health(),
        }
        if app.state.background_worker_enabled and worker is not None and worker.done():
            # The draft loop died: fail the check while the process drains and exits
            # non-zero so Fly routes away immediately and restarts this Machine.
            return JSONResponse({**body, "status": "worker_stopped"}, status_code=503)
        return body

    # Reading the revision scripts takes a while; they never change in a running build.
    from functools import cache
    from anteroom.services.schema_migrations import head_revision
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

        from anteroom.database import engine
        if engine.dialect.name != "postgresql":
            return {"status": "ok"}
        try:
            with engine.connect() as connection, connection.begin():
                # LOCAL so a reused connection does not keep the one-second limit.
                connection.execute(text("SET LOCAL statement_timeout = '1000'"))
                revision = connection.scalar(text("SELECT anteroom_private.schema_revision()"))
        except SQLAlchemyError as exc:
            from anteroom.services.error_alerts import note_cause
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
