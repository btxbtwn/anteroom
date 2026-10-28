from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.middleware.gzip import GZipMiddleware

from vendoo_studio.config import HOST, PORT, CORS_ORIGINS, frontend_dist_dir
from vendoo_studio.database import init_db, SessionLocal
from vendoo_studio.static_files import FrontendFiles
from vendoo_studio.routes import health, conversations, photos, listings, jobs, settings, extension, chat, updates, desktop, imports, listing_commands
from vendoo_studio.routes import ad_spend, analytics, etsy, assistant, attachments, backups, boxes, browser, catalog, changelog, marketplace_accounts, measurements, sale_calendar, scout, sourcing, vendoo_api, suggestions
from vendoo_studio.version import app_version
from vendoo_studio.routes import marketplace_listings, marketplace_schemas, native_marketplaces
from vendoo_studio.routes import ebay_offers, listing_promotions, promotion_automations
from vendoo_studio.routes import ebay
from vendoo_studio.routes import chrome_import, similar_items
from vendoo_studio.routes import ai_keys, sales_ledger, bulk_delete, bulk_edit, bulk_marketplace, listing_copies, listing_templates
from vendoo_studio.routes import automations as automations_route
from vendoo_studio.routes import errors as errors_route
from vendoo_studio.routes import listing_videos


@asynccontextmanager
async def lifespan(app: FastAPI):
    from vendoo_studio.services.error_log import install as install_error_log

    install_error_log()
    init_db()
    try:
        from vendoo_studio.services.keychain import warm_keychain
        warm_keychain()
    except Exception:
        pass
    try:
        from vendoo_studio.services.category_tree_seed import ensure_seeded_category_trees
        ensure_seeded_category_trees()
    except Exception:
        pass
    try:
        from vendoo_studio.services.chrome_bridge import install_bundled_extension
        install_bundled_extension()
    except Exception:
        pass
    try:
        from vendoo_studio.services.backups import start_snapshot_timer
        start_snapshot_timer()
    except Exception:
        pass
    try:
        from vendoo_studio.services.box_scout import start_sourcing_timer
        start_sourcing_timer()
    except Exception:
        pass
    try:
        from vendoo_studio.services.job_watchdog import start_job_watchdog
        start_job_watchdog()
    except Exception:
        pass
    try:
        from vendoo_studio.services.native_field_fill import start_category_fills
        start_category_fills()
    except Exception:
        pass
    try:
        from vendoo_studio.services.automation_scheduler import start_automation_scheduler
        start_automation_scheduler()
    except Exception:
        pass
    try:
        from vendoo_studio.services.maintenance import prune_event_bloat
        import logging
        import threading

        def _prune_event_bloat() -> None:
            try:
                with SessionLocal() as db:
                    pruned = prune_event_bloat(db)
                if pruned["deleted"]:
                    logging.getLogger("vendoo_studio").info(
                        "Pruned %s job event(s) nothing reads", pruned["deleted"]
                    )
            except Exception:
                pass

        threading.Thread(target=_prune_event_bloat, name="prune-job-events", daemon=True).start()
    except Exception:
        pass
    try:
        from vendoo_studio.services.marketplace_accounts import start_listening
        start_listening()
    except Exception:
        pass
    try:
        from vendoo_studio.services.chrome_sales import start_chrome_sales
        start_chrome_sales()
    except Exception:
        pass
    from vendoo_studio.providers import chatgpt_codex, claude_sessions

    from vendoo_studio.services import ebay_offers as offer_service
    offer_service.start()
    from vendoo_studio.services.ebay_actions import recover as recover_ebay_actions
    with SessionLocal() as db:
        recover_ebay_actions(db)
        from vendoo_studio.services.listing_promotions import mark_interrupted as recover_promotions

        # A share or offer that was sending when Anteroom stopped is unknown, never resent.
        recover_promotions(db)
    from vendoo_studio.services import promotion_automations as promotion_automation_service
    promotion_automation_service.start()

    claude_sessions.keep_warm_on_this_loop()
    chatgpt_codex.keep_connection_on_this_loop()
    yield
    await offer_service.stop()
    await promotion_automation_service.stop()
    try:
        await claude_sessions.close_warm_processes()
        await chatgpt_codex.close_connection()
    except Exception:
        pass
    try:
        from vendoo_studio.services.relay_client import stop_stream
        stop_stream()
    except Exception:
        pass
    try:
        from vendoo_studio.services.backups import stop_snapshot_timer
        stop_snapshot_timer()
    except Exception:
        pass
    try:
        from vendoo_studio.services.box_scout import stop_sourcing_timer
        stop_sourcing_timer()
    except Exception:
        pass
    try:
        from vendoo_studio.services.job_watchdog import stop_job_watchdog
        stop_job_watchdog()
    except Exception:
        pass
    try:
        from vendoo_studio.services.automation_scheduler import stop_automation_scheduler
        stop_automation_scheduler()
    except Exception:
        pass
    try:
        from vendoo_studio.services.chrome_sales import stop_chrome_sales
        stop_chrome_sales()
    except Exception:
        pass
    # Last thing before the process goes: pruning frees pages inside the file,
    # and only VACUUM hands them back to the disk. Nobody is waiting on it here.
    try:
        from vendoo_studio.services.maintenance import vacuum_database
        vacuum_database()
    except Exception as exc:
        import logging
        logging.getLogger("vendoo_studio").warning("Could not vacuum the database: %s", exc)


app = FastAPI(
    title="Anteroom",
    version=app_version(),
    description="Local web app for generating and automating Vendoo marketplace listings",
    lifespan=lifespan,
)

class _VendooOffMiddleware:
    """Answer Vendoo routes with 409 while the seller has Vendoo turned off.

    Added before CORS so a browser still receives the CORS headers on that 409.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from vendoo_studio.services.user_settings import vendoo_enabled
            from vendoo_studio.services.vendoo_gate import VENDOO_OFF_DETAIL, vendoo_request_blocked

            path = scope.get("path") or ""
            method = scope.get("method") or "GET"
            if vendoo_request_blocked(method, path) and not vendoo_enabled():
                response = JSONResponse({"detail": VENDOO_OFF_DETAIL}, status_code=409)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(_VendooOffMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)
# The inventory, the buy list and the bundle are each around a megabyte of
# text, and over Tailscale a phone pulls them across a mobile connection.
# Live event streams and photos are left alone by the middleware's defaults.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(health.router)
app.include_router(marketplace_accounts.router)
app.include_router(ebay_offers.router)
app.include_router(listing_promotions.router)
app.include_router(promotion_automations.router)
app.include_router(ebay.router)
app.include_router(native_marketplaces.router)
app.include_router(marketplace_schemas.router)
app.include_router(marketplace_listings.router)
app.include_router(conversations.router)
app.include_router(listing_commands.router)
app.include_router(photos.router)
app.include_router(listing_videos.router)
app.include_router(measurements.router)
app.include_router(listings.router)
app.include_router(jobs.router)
app.include_router(imports.router)
app.include_router(ai_keys.router)
app.include_router(settings.router)
app.include_router(extension.router)
app.include_router(chat.router)
app.include_router(catalog.router)
app.include_router(suggestions.router)
app.include_router(analytics.router)
app.include_router(sales_ledger.router)
app.include_router(bulk_edit.router)
app.include_router(bulk_marketplace.router)
app.include_router(bulk_delete.router)
app.include_router(listing_copies.router)
app.include_router(listing_templates.router)
app.include_router(etsy.router)
app.include_router(chrome_import.router)
app.include_router(similar_items.router)
app.include_router(sourcing.router)
app.include_router(boxes.router)
app.include_router(sale_calendar.router)
app.include_router(ad_spend.router)
app.include_router(assistant.router)
app.include_router(automations_route.router)
app.include_router(errors_route.router)
app.include_router(attachments.router)
app.include_router(scout.router)
app.include_router(browser.router)
app.include_router(vendoo_api.router)
app.include_router(backups.router)
app.include_router(updates.router)
app.include_router(changelog.router)
app.include_router(desktop.router)

dist_dir = frontend_dist_dir()
if dist_dir.exists():
    app.mount("/", FrontendFiles(directory=str(dist_dir), html=True), name="static")


def main():
    import uvicorn
    uvicorn.run(
        "vendoo_studio.main:app",
        host=HOST,
        port=PORT,
        reload=os.environ.get("VENDOO_STUDIO_DEV", "") == "1",
    )


if __name__ == "__main__":
    main()
