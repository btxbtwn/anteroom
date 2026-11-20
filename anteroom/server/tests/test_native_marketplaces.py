from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from anteroom.services import listing_persistence
from anteroom.database import Base, get_db
from anteroom.main import app
from anteroom.models.conversation import utcnow
from anteroom.repositories.marketplace_listings import MarketplaceListingRepo
from anteroom.repositories.queries import ConversationRepo, JobRepo, ListingRepo
from anteroom.services import api_job_lock, native_marketplaces as native
from anteroom.services.api_job_lock import claim_api_job, job_is_live, release_api_job
from anteroom.services.browser_bridge import BrowserBridgeError
from anteroom.services.job_watchdog import fail_stalled_jobs
from anteroom.services.photo_store import LocalPhotoStore
from anteroom.services.vendoo_import import merge_notes, parse_notes

URLS = {"mercari": "https://www.mercari.com/sell/draft/testNativeDraft/",
        "depop": "https://www.depop.com/sellinghub/drafts/edit/123456/"}
LIVE = {"mercari": "https://www.mercari.com/us/item/m123456789/", "depop": "https://www.depop.com/products/fixture-shirt-durable-123/"}
POSH_URL = "https://poshmark.com/listing/6adc371572e62463d992ffef"
LISTING = {"title": "ANTEROOM DRAFT CHECK - DO NOT PUBLISH", "description": "Private test draft.", "price": 12,
           "size": "M",
           "weight_oz": 8, "package_dimensions_in": "12x9x2",
           "marketplace_category_ids": {"ebay": "11450", "poshmark": "5c3c3d4a6a2b3c4d5e6f7081", "mercari": "1934", "depop": "menswear_tshirts"},
           "direct_category_ids": {"ebay": "11450", "poshmark": "01008c10d97b4e1245005764/07008c10d97b4e1245005764/33009813d97b4e3995005764",
                                   "mercari": "1934", "depop": "menswear_tshirts"},
           "mercari_specifics": {"shipsFrom": "70125", "shippingClassId": "2550",
                                "package": {"fitsShoebox": "no", "pounds": 1}},
           "depop_specifics": {"parcelSize": "Medium"}}


class NativeMarketplacesTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        store = LocalPhotoStore(directory)
        for name in ("test.jpg", "back.jpg", "collage.jpg"):
            store.put(name, b"test photo")
        self.enterContext(patch("anteroom.services.photo_store._store", store))
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine)
        self.db = self.sessions()
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.conv = ConversationRepo(self.db).create(title=LISTING["title"])
        self.rev = listing_persistence.save_revision(self.db, self.conv.id, LISTING, "model")
        listing_persistence.add_photo(self.db, self.conv.id, "test.jpg", "test.jpg", "image/jpeg", 1)
        self.base = f"/api/conversations/{self.conv.id}/native-marketplaces"
        self.bridge = self.enterContext(patch("anteroom.services.native_marketplaces.browser_bridge.request", new_callable=AsyncMock))
        self.manager = SimpleNamespace(connected=True, capabilities=frozenset({"marketplace.actions"}), send_message=AsyncMock(return_value=True))
        self.enterContext(patch("anteroom.services.extension_connection.extension_manager", self.manager))
        self.enterContext(patch("anteroom.database.SessionLocal", self.sessions))
        self.enterContext(patch.object(native, "SETTLE_POLL_S", 0))
        self.launched = []
        self.enterContext(patch.object(native, "_launch", self.launched.append))

    def tearDown(self):
        for coro in self.launched:
            coro.close()
        app.dependency_overrides.clear()
        self.db.close()

    def test_fixture_create_generate_edit_confirm_list_failure_retry_and_reload(self):
        """Simulated publication only: real routes/storage, fake AI and bridge, no sockets."""
        import io
        from contextlib import ExitStack

        from httpx import ASGITransport, AsyncClient
        from PIL import Image

        from test_listing_generate import FakeProvider
        from anteroom.services import streaming

        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Fixture workflow cannot open a socket")))
        created = self.client.post("/api/conversations", json={"title": "Disposable audit item"})
        self.assertEqual(created.status_code, 200, created.text)
        self.conv = ConversationRepo(self.db).get(created.json()["id"])
        self.base = f"/api/conversations/{self.conv.id}/native-marketplaces"
        image = io.BytesIO()
        Image.new("RGB", (80, 80), "blue").save(image, format="JPEG")
        uploaded = self.client.post(f"/api/conversations/{self.conv.id}/photos", files={
            "files": ("fixture.jpg", image.getvalue(), "image/jpeg"),
        })
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(len(ConversationRepo(self.db).get_photos(self.conv.id)), 1)
        provider = FakeProvider(chunks=[json.dumps({**LISTING, "brand": "Fixture", "condition": "Good"})])

        async def generate():
            finishing = []
            spawn = streaming.spawn

            def finish(coro):
                task = spawn(coro)
                finishing.append(task)
                return task

            with ExitStack() as fixtures:
                for target, value in (
                    ("routes.chat.get_listing_provider", provider),
                    ("routes.chat.get_photo_provider", provider),
                    ("services.listing_generation.load_skill_rules", "Fixture listing rules"),
                    ("services.listing_generation.comps_search_available", False),
                ):
                    fixtures.enter_context(patch(f"anteroom.{target}", return_value=value))
                fixtures.enter_context(patch("anteroom.services.listing_generation.research_sold_comps", new=AsyncMock(return_value="")))
                fixtures.enter_context(patch("anteroom.services.listing_generation.prepare_generation_schema", new=AsyncMock(return_value={})))
                fixtures.enter_context(patch("anteroom.services.listing_generation.spawn", side_effect=finish))
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:4329") as client:
                    response = await client.post(f"/api/conversations/{self.conv.id}/generate")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn("[DONE]", response.text)
                await asyncio.wait_for(asyncio.gather(*finishing), timeout=30)
            await streaming.reset_generations()

        asyncio.run(generate())
        self.db.expire_all()
        saved = self.client.get(f"/api/conversations/{self.conv.id}/listing").json()
        edited = {**saved["listing"], "title": "Edited disposable audit shirt"}
        response = self.client.put(f"/api/conversations/{self.conv.id}/listing", json={
            "listing": edited, "expected_revision_id": saved["current_revision_id"],
        })
        self.assertEqual(response.status_code, 200, response.text)
        validation = self.client.post(f"/api/conversations/{self.conv.id}/listing/validate")
        self.assertEqual(validation.status_code, 200, validation.text)
        # Fake generation intentionally leaves marketplace fields unresolved.
        # Validation must expose those gaps; the adapter result below is simulated,
        # not evidence that this incomplete draft is ready for a real marketplace.
        self.assertFalse(validation.json()["can_send"])
        self.assertTrue(validation.json()["errors"])
        self.db.expire_all()
        self.rev = ListingRepo(self.db).latest_revision(self.conv.id)
        body = {"revision_id": self.rev.id}
        self.assertEqual(self.client.post(f"{self.base}/poshmark/list", json=body).status_code, 400)
        self.assertEqual(self.client.post(f"{self.base}/all/list", json={**body, "confirm": True}).status_code, 422)
        self.bridge.assert_not_awaited()
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Fixture: choose a size.", "error_code": "rejected"}
        self.assertEqual(self.list("poshmark")["state"], "not_saved")
        self.assertIn("choose a size", self.status()["poshmark"]["error"])
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        self.assertEqual(self.list("poshmark")["state"], "published")
        payload = self.bridge.call_args.args[2]
        self.assertEqual((payload["action"], payload["confirm"]), ("list", True))
        self.assertEqual(payload["listing"]["title"], "Edited disposable audit shirt")
        self.assertEqual(len(payload["photos"]), 1)
        self.assertEqual(self.actions(), ["list", "list"])
        reopened = TestClient(app)
        status = reopened.get(self.base).json()["poshmark"]
        self.assertEqual((status["state"], status["listing_url"]), ("published", POSH_URL))
        self.db.expire_all()
        from anteroom.services.item_status import status_for

        self.assertEqual(status_for(self.db, self.conv.id, parse_notes(self.conv.notes)).status, "active")

    def test_current_reprice_saves_reloads_and_recovers_from_invalid_and_stale_edits(self):
        """Historical 0.1.492 lead: current local API, two independent editor reads."""
        other_editor = TestClient(app)
        url = f"/api/conversations/{self.conv.id}/listing"
        first = self.client.get(url).json()
        stale = other_editor.get(url).json()
        saved = self.client.put(url, json={
            "listing": {**first["listing"], "price": 37},
            "expected_revision_id": first["current_revision_id"],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(other_editor.get(url).json()["listing"]["price"], 37)
        conflict = other_editor.put(url, json={
            "listing": {**stale["listing"], "price": 45},
            "expected_revision_id": stale["current_revision_id"],
        })
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertIn("Reload", conflict.json()["detail"])
        current = other_editor.get(url).json()
        self.assertEqual(current["listing"]["price"], 37)
        invalid = other_editor.put(url, json={
            "listing": {**current["listing"], "price": -5},
            "expected_revision_id": current["current_revision_id"],
        })
        # Incomplete drafts remain saveable; validation prevents silent readiness.
        self.assertEqual(invalid.status_code, 200, invalid.text)
        self.assertTrue(any(error["field"] == "price" for error in invalid.json()["validation"]["errors"]))
        current = other_editor.get(url).json()
        recovered = other_editor.put(url, json={
            "listing": {**current["listing"], "price": 39},
            "expected_revision_id": current["current_revision_id"],
        })
        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertFalse(any(error["field"] == "price" for error in recovered.json()["validation"]["errors"]))
        self.assertEqual(TestClient(app).get(url).json()["listing"]["price"], 39)
        self.bridge.assert_not_awaited()
        self.assertFalse(self.launched)

    def test_depop_publish_without_identity_stays_unconfirmed_and_cannot_republish(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("offline fixture")))

        async def answer(_job, kind, payload, **_kwargs):
            self.assertEqual(kind, "marketplace.execute")
            if payload["action"] == "save_draft":
                return {"ok": True, "state": "draft", "draft_url": URLS["depop"]}
            return {"ok": True, "state": "published", "draft_url": URLS["depop"]}

        self.bridge.side_effect = answer
        result = self.list("depop")
        self.assertEqual(result["state"], "unknown", result)
        self.assertEqual(result["error_code"], "uncertain")
        retry = self.client.post(f"{self.base}/depop/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(retry.status_code, 409, retry.text)
        self.assertEqual(self.actions(), ["save_draft", "publish"])
        delist = self.client.post(f"/api/conversations/{self.conv.id}/marketplace-listings/delist",
                                 json={"marketplaces": ["depop"], "confirm": True})
        self.assertEqual(delist.status_code, 409, delist.text)

        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {
            "ok": True, "state": "published", "draft_url": URLS["depop"], "listing_url": LIVE["depop"],
        }}
        checked = self.client.post(f"{self.base}/depop/check")
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual(checked.json()["state"], "published")
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")
        self.assertEqual(self.actions(), ["save_draft", "publish"])
        fresh = TestClient(app).get(f"/api/conversations/{self.conv.id}/marketplace-listings").json()["depop"]
        self.assertEqual(fresh["listing_id"], "fixture-shirt-durable-123")

    def test_depop_simulated_publish_identity_reload_delist_and_independent_readback(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("offline fixture")))
        product = "fixture-shirt-durable-123"
        url = f"https://www.depop.com/products/{product}/"
        marketplace_state = {}
        from fastapi import FastAPI

        simulated_marketplace = FastAPI()

        @simulated_marketplace.get("/products/{slug}")
        def read_marketplace(slug: str):
            return {"id": slug, "listed": marketplace_state.get(slug, False)}

        independent_reader = TestClient(simulated_marketplace)

        def read_in_new_process():
            import os
            from pathlib import Path
            import sqlite3
            import subprocess
            import sys

            folder = Path(self.enterContext(TemporaryDirectory()))
            database = folder / "anteroom.db"
            raw = self.db.get_bind().raw_connection()
            try:
                with sqlite3.connect(database) as destination:
                    raw.driver_connection.backup(destination)
            finally:
                raw.close()
            env = {**os.environ, "ANTEROOM_DATA_DIR": str(folder),
                   "ANTEROOM_DATABASE_URL": f"sqlite:///{database}", "ANTEROOM_SANDBOX": "1",
                   "ANTEROOM_PORT": "4599", "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
            env.pop("ANTEROOM_TEST_DATABASE_URL", None)
            script = """
import json, socket, sys
socket.socket.connect = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("offline fixture"))
from fastapi.testclient import TestClient
from anteroom.main import app
with TestClient(app, base_url="http://127.0.0.1:4599") as client:
    response = client.get(sys.argv[1])
    assert response.status_code == 200, response.text
    print("FIXTURE_JSON=" + json.dumps(response.json()))
"""
            read = subprocess.run([sys.executable, "-c", script, base], env=env,
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(read.returncode, 0, read.stderr)
            return json.loads(next(line.removeprefix("FIXTURE_JSON=") for line in read.stdout.splitlines()
                                   if line.startswith("FIXTURE_JSON=")))

        async def adapter(_job, kind, payload, **_kwargs):
            if kind == "marketplace.delete":
                self.assertEqual(payload, {"marketplace": "depop", "listing_id": product, "confirm": True})
                self.assertTrue(marketplace_state[product])
                marketplace_state[product] = False
                return {"ok": True, "state": "deleted"}
            self.assertEqual(kind, "marketplace.execute")
            if payload["action"] == "save_draft":
                return {"ok": True, "state": "draft", "draft_url": URLS["depop"]}
            self.assertEqual((payload["action"], payload["confirm"]), ("publish", True))
            marketplace_state[product] = True
            return {"ok": True, "state": "published", "draft_url": URLS["depop"], "listing_url": url}

        self.bridge.side_effect = adapter
        self.assertEqual(self.list("depop")["state"], "published")
        self.db.expire_all()
        fresh_session = self.sessions()
        self.addCleanup(fresh_session.close)
        app.dependency_overrides[get_db] = lambda: fresh_session
        reopened = TestClient(app)
        base = f"/api/conversations/{self.conv.id}/marketplace-listings"
        persisted = reopened.get(base).json()["depop"]
        self.assertEqual((persisted["listing_id"], persisted["url"]), (product, url))
        restarted = read_in_new_process()["depop"]
        self.assertEqual((restarted["listing_id"], restarted["url"], restarted["state"]), (product, url, "live"))
        # Independent read from the simulated marketplace, not Anteroom's mark.
        self.assertEqual(independent_reader.get(f"/products/{product}").json(), {"id": product, "listed": True})
        deleted = reopened.post(f"{base}/delist", json={"marketplaces": ["depop"], "confirm": True})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["depop"]["state"], "deleted")
        self.db.expire_all()
        self.assertEqual(TestClient(app).get(base).json()["depop"]["state"], "deleted")
        self.assertEqual(read_in_new_process()["depop"]["state"], "deleted")
        self.assertEqual(independent_reader.get(f"/products/{product}").json(), {"id": product, "listed": False})

    def test_legacy_published_without_link_is_unconfirmed_on_read_and_recovers_without_republish(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("offline fixture")))

        async def publish(_job, _kind, payload, **_kwargs):
            if payload["action"] == "save_draft":
                return {"ok": True, "state": "draft", "draft_url": URLS["depop"]}
            return self.published("depop")

        self.bridge.side_effect = publish
        self.assertEqual(self.list("depop")["state"], "published")
        records = MarketplaceListingRepo(self.db)
        # A disposable fixture represents the old stored shape, not a data repair.
        records.save(self.conv.id, "depop", {"listing_url": None})
        before = records.row(self.conv.id, "depop").updated_at
        self.assertEqual(self.status()["depop"]["state"], "unknown")
        self.assertEqual(records.summaries(self.conv.id)[self.conv.id]["depop"]["state"], "unknown")
        self.assertNotIn(self.conv.id, records.live_revisions(self.conv.id))
        raw = records.row(self.conv.id, "depop")
        self.assertEqual((raw.state, raw.updated_at), ("published", before))
        retry = self.client.post(f"{self.base}/depop/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(retry.status_code, 409, retry.text)
        self.assertEqual(self.actions(), ["save_draft", "publish"])
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {
            "ok": True, "state": "published", "draft_url": URLS["depop"], "listing_url": LIVE["depop"],
        }}
        response = self.client.post(f"{self.base}/depop/check")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "published")
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")
        self.assertEqual(self.actions(), ["save_draft", "publish"])
        self.assertEqual(TestClient(app).get(self.base).json()["depop"]["listing_url"], LIVE["depop"])

    def test_legacy_without_saved_job_requires_external_verification_before_manual_recovery(self):
        from fastapi import FastAPI

        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("offline fixture")))
        product = "fixture-historical-shirt-123"
        url = f"https://www.depop.com/products/{product}/"
        marketplace = {product: True}
        remote = FastAPI()

        @remote.get("/products/{slug}")
        def independently_read(slug: str):
            return {"listed": marketplace[slug]}

        reader = TestClient(remote)
        records = MarketplaceListingRepo(self.db)
        records.save(self.conv.id, "depop", {"state": "published"})
        self.assertEqual(self.status()["depop"]["state"], "unknown")
        check = self.client.post(f"{self.base}/depop/check")
        self.assertEqual(check.status_code, 409, check.text)
        self.assertIn("Verify the item's status directly on Depop", check.json()["detail"])
        self.bridge.assert_not_awaited()
        base = f"/api/conversations/{self.conv.id}/marketplace-listings"
        unverified = self.client.post(f"{base}/depop/mark", json={"state": "listed"})
        self.assertEqual(unverified.status_code, 400, unverified.text)
        self.assertEqual(reader.get(f"/products/{product}").json(), {"listed": True})
        marked = self.client.post(f"{base}/depop/mark", json={"state": "listed", "url": url})
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertEqual(marked.json()["depop"]["listing_id"], product)
        self.bridge.assert_not_awaited()

        async def delete(_job, kind, payload, **_kwargs):
            self.assertEqual(kind, "marketplace.delete")
            self.assertEqual(payload, {"marketplace": "depop", "listing_id": product, "confirm": True})
            marketplace[product] = False
            return {"ok": True, "state": "deleted"}

        self.bridge.side_effect = delete
        removed = self.client.post(f"{base}/delist", json={"marketplaces": ["depop"], "confirm": True})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["depop"]["state"], "deleted", removed.text)
        with self.sessions() as read_session:
            app.dependency_overrides[get_db] = lambda: read_session
            self.assertEqual(TestClient(app).get(base).json()["depop"]["state"], "deleted")
        self.assertEqual(reader.get(f"/products/{product}").json(), {"listed": False})
        self.assertEqual(self.actions(), [])

    def test_missing_and_contradictory_adapter_states_never_verify_success(self):
        for market, action, draft_url, live_url in [
            ("depop", "publish", URLS["depop"], LIVE["depop"]),
            ("mercari", "publish", URLS["mercari"], LIVE["mercari"]),
            ("poshmark", "list", None, POSH_URL),
        ]:
            for state in (None, "draft", "unknown", "not_listed"):
                with self.subTest(market=market, state=state):
                    reply = {"ok": True, "listing_url": live_url, "draft_url": draft_url}
                    if state is not None:
                        reply["state"] = state
                    result = native._outcome(market, action, {}, reply)
                    self.assertEqual((result["state"], result["error_code"]), ("unknown", "uncertain"))
        saved = native._outcome("depop", "save_draft", {}, {
            "ok": True, "state": "draft", "draft_url": URLS["depop"],
        })
        self.assertEqual(saved["state"], "draft")
        self.assertNotIn("listing_url", saved)

    # --- helpers ---------------------------------------------------------------

    def test_form_preparation_copies_the_listing_once_and_keeps_input_independent(self):
        import copy
        from anteroom.services.direct_preflight import prepare

        copies = []

        class ListingInput(dict):
            def __deepcopy__(self, memo):
                copies.append(True)
                result = ListingInput(copy.deepcopy(dict(self), memo))
                memo[id(self)] = result
                return result

        original = copy.deepcopy(LISTING)
        original['mercari_specifics'] = ListingInput(original['mercari_specifics'])
        saved = copy.deepcopy(original)
        copies.clear()
        snapshot = prepare(self.db, self.conv, original, 'mercari')
        self.assertEqual(len(copies), 1)
        snapshot['mercari_specifics']['shipsFrom'] = '10001'
        self.assertEqual(original, saved)

    def test_form_preparation_and_preflight_share_saved_native_forms(self):
        # Saved forms can be megabytes. Copying them for every form and every
        # marketplace kept a single CPU busy inside open transactions, until the
        # hosted connection pool ran out (2026-11-09).
        import copy
        from anteroom.services import native_contract
        from anteroom.services.direct_preflight import prepare

        listing = copy.deepcopy(LISTING)
        form = {"category_id": "menswear_tshirts", "page_labels": True, "fields": [
            {"path": "depop_specifics.size", "label": "Size", "required": True,
             "selection_only": True, "options": ["M", "L"]}]}
        listing[native_contract.KEY] = native_contract.capture(listing, {"depop": form}, ["depop"])
        revision = listing_persistence.save_revision(self.db, self.conv.id, listing, "native_schema")
        forms = revision.listing_json[native_contract.KEY]
        saved = copy.deepcopy(forms)

        for marketplace in native.NAMES:
            snapshot = prepare(self.db, self.conv, revision.listing_json, marketplace)
            self.assertIs(snapshot[native_contract.KEY], forms)
        seen = []
        gaps = native.direct_preflight.gaps

        def observe(snapshot, marketplace, context=None):
            seen.append(snapshot[native_contract.KEY])
            return gaps(snapshot, marketplace, context)

        with patch.object(native.direct_preflight, "gaps", observe):
            native.preflight(self.conv.id, self.db)
        self.assertEqual(len(seen), len(native.NAMES))
        self.assertTrue(all(shared is forms for shared in seen))
        self.assertEqual(forms, saved)

    def test_preflight_reads_photos_once_for_all_marketplaces(self):
        from sqlalchemy import event

        queries = []

        def observe(_conn, _cursor, statement, _parameters, _context, _many):
            if 'FROM photos' in statement:
                queries.append(statement)

        event.listen(self.db.bind, 'before_cursor_execute', observe)
        try:
            result = native.preflight(self.conv.id, self.db)
        finally:
            event.remove(self.db.bind, 'before_cursor_execute', observe)
        self.assertEqual(set(result['marketplaces']), set(native.NAMES))
        self.assertEqual(len(queries), 1)

    def run_jobs(self):
        while self.launched:
            asyncio.run(self.launched.pop(0))
        self.db.expire_all()

    def list(self, platform="mercari", **extra):
        response = self.client.post(f"{self.base}/{platform}/list", json={"revision_id": self.rev.id, "confirm": True, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["running"])
        self.run_jobs()
        return self.status()[platform]

    def status(self):
        return self.client.get(self.base).json()

    def test_editor_preflight_uses_saved_native_requirements_without_chrome_or_category_writes(self):
        import copy
        from anteroom.services import native_contract

        snapshot = copy.deepcopy(LISTING)
        snapshot["direct_category_ids"] = {"depop": "menswear_tshirts"}
        snapshot = native.direct_preflight.prepare(self.db, self.conv, snapshot, "depop")
        snapshot["depop_specifics"]["size"] = ""
        form = {"category_id": "menswear_tshirts", "page_labels": True, "fields": [
            {"path": "depop_specifics.size", "label": "Size", "required": True,
             "selection_only": True, "options": ["M", "L"]}]}
        snapshot[native_contract.KEY] = native_contract.capture(snapshot, {"depop": form}, ["depop"])
        revision = listing_persistence.save_revision(self.db, self.conv.id, snapshot, "native_schema")
        before = copy.deepcopy(revision.listing_json)
        read = AsyncMock(side_effect=AssertionError("Chrome or remote catalog used during editor preflight"))
        with patch("anteroom.services.chrome_catalog.live_preview", new=read):
            for connected in (False, True):
                self.manager.connected = connected
                response = self.client.get(f"{self.base}/preflight")
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()["marketplaces"]["depop"]
                self.assertFalse(result["ready"])
                self.assertTrue(any(gap["field"] == "depop_specifics.size" for gap in result["gaps"]), result)
        read.assert_not_awaited()
        self.bridge.assert_not_awaited()
        self.db.expire_all()
        self.assertEqual(ListingRepo(self.db).get_revision(revision.id).listing_json, before)
        self.assertEqual(ListingRepo(self.db).get_current(self.conv.id).current_revision_id, revision.id)
        self.assertEqual(JobRepo(self.db).list_by_conversation(self.conv.id), [])

    def record(self, platform):
        return MarketplaceListingRepo(self.db).get(self.conv.id, platform)

    def actions(self):
        return [call.args[2]["action"] for call in self.bridge.call_args_list if call.args[1] == "marketplace.execute"]

    def published(self, platform):
        return {"ok": True, "state": "published", "draft_url": URLS[platform], **({"listing_url": LIVE[platform]} if LIVE[platform] else {})}

    # --- listing -----------------------------------------------------------------

    def test_local_field_gaps_do_not_hide_a_marketplace_rejection(self):
        message = "Please select a valid size."
        fields = [{"field": "mercari_specifics.size", "message": message}]
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Mercari rejected the listing.",
                                   "error_code": "missing_fields", "marketplace_message": message, "fields": fields}
        with patch.object(native.direct_preflight, "gaps", return_value=[
                {"field": "mercari_specifics.size", "message": "Size needs review.", "blocking": True}]):
            response = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["running"])
        self.run_jobs()
        self.assertEqual(self.actions(), ["save_draft"])
        record = self.record("mercari")
        self.assertEqual(record["marketplace_message"], message)
        self.assertEqual(record["error_fields"], fields)

        # A seller correction remains saveable after a rejection and the next
        # confirmed attempt uses that new revision, rather than a stale form.
        corrected = {**LISTING, "size": "L"}
        self.rev = listing_persistence.save_revision(self.db, self.conv.id, corrected, "user_form")

        async def accepted(_job, _kind, payload, **_kwargs):
            self.assertEqual(payload["listing"]["size"], "L")
            if payload["action"] == "save_draft":
                return {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}
            return self.published("mercari")

        self.bridge.side_effect = accepted
        result = self.list("mercari")
        self.assertEqual(result["state"], "published", result)
        self.assertFalse(result.get("marketplace_message"))

    def test_list_without_a_category_starts_a_marketplace_attempt(self):
        listing = {**LISTING, "marketplace_category_ids": {}, "marketplace_category_objects": {},
                   "marketplace_categories": {}, "direct_category_ids": {}}
        revision = listing_persistence.save_revision(self.db, self.conv.id, listing, "user_form")
        self.bridge.reset_mock()
        self.bridge.return_value = {"ok": False, "safe_to_retry": True,
                                    "error": "Fixture: choose a category.", "error_code": "missing_fields"}
        response = self.client.post(f"{self.base}/mercari/list", json={"revision_id": revision.id, "confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["running"])
        self.run_jobs()
        self.assertIn("save_draft", self.actions())

    def test_save_draft_runs_one_private_step_and_never_publishes_on_check(self):
        for platform in URLS:
            with self.subTest(platform=platform):
                self.bridge.reset_mock()
                self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": URLS[platform]}
                response = self.client.post(f"{self.base}/{platform}/draft", json={"revision_id": self.rev.id})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["running"])
                self.run_jobs()
                self.assertEqual(self.actions(), ["save_draft"])
                self.assertIs(self.bridge.call_args.args[2]["confirm"], False)
                self.assertEqual(self.record(platform)["state"], "draft")
                # Re-saving updates the same draft, with no publish step.
                response = self.client.post(f"{self.base}/{platform}/draft", json={"revision_id": self.rev.id})
                self.assertEqual(response.status_code, 200, response.text)
                self.run_jobs()
                self.assertEqual(self.actions(), ["save_draft", "save_draft"])
                self.assertEqual(self.bridge.call_args.args[2]["draft_url"], URLS[platform])

    def test_an_approved_job_keeps_its_photos_after_the_item_photos_are_replaced(self):
        from anteroom.services.photo_store import photo_store
        from anteroom.services.listing_delete import delete_listing

        repo = ConversationRepo(self.db)
        [original] = repo.get_photos(self.conv.id)
        self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}
        reply = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(reply.status_code, 200, reply.text)
        job_id = reply.json()["job_id"]
        pinned = JobRepo(self.db).get(job_id).listing_snapshot["_approved_photos"][0]["stored_filename"]
        listing_persistence.delete_photo(repo.db, self.conv.id, original.id)
        photo_store().delete("test.jpg")
        photo_store().put("new.jpg", b"Different product")
        replacement = listing_persistence.add_photo(repo.db, self.conv.id, "new.jpg", "new.jpg", "image/jpeg", 17)
        self.run_jobs()
        payload = self.bridge.call_args.args[2]
        self.assertEqual([photo["id"] for photo in payload["photos"]], [original.id])
        image = self.client.get(f"/api/jobs/{job_id}/photos/{original.id}")
        self.assertEqual(image.status_code, 200, image.text)
        self.assertEqual(image.content, b"test photo")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}/photos/{replacement.id}").status_code, 404)
        delete_listing(self.db, self.conv.id)
        self.assertFalse(photo_store().exists(pinned))

    def test_a_missing_photo_prevents_dispatch(self):
        from anteroom.services.photo_store import photo_store

        photo_store().delete("test.jpg")
        reply = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(reply.status_code, 409, reply.text)
        self.assertEqual(self.launched, [])
        self.assertEqual(JobRepo(self.db).list_by_conversation(self.conv.id), [])

    def test_clear_keeps_approved_files_shared_by_the_action_and_its_check(self):
        from anteroom.services.listing_delete import wipe_contents
        from anteroom.services.photo_store import photo_store

        draft = {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}
        self.bridge.return_value = draft
        saved = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.run_jobs()
        job_id = saved.json()["job_id"]
        key = JobRepo(self.db).get(job_id).listing_snapshot["_approved_photos"][0]["stored_filename"]
        self.bridge.return_value = {"finished": True, "result": draft}
        checked = self.client.post(f"{self.base}/mercari/check")
        self.assertEqual(checked.status_code, 200, checked.text)
        wipe_contents(self.db, self.conv.id, keep_photos=True, keep_marketplace_listings=True)
        self.db.commit()
        self.assertTrue(photo_store().exists(key))
        self.assertIsNotNone(JobRepo(self.db).get(job_id))

    def test_listing_a_saved_draft_refreshes_a_photo_edited_since_the_save(self):
        from anteroom.services.photo_store import photo_store

        self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}
        reply = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.run_jobs()
        [photo] = ConversationRepo(self.db).get_photos(self.conv.id)
        photo_store().put("edited.jpg", b"Edited photo")
        photo.stored_filename = "edited.jpg"
        self.db.commit()
        self.bridge.reset_mock()
        self.bridge.side_effect = [
            {"ok": True, "state": "draft", "draft_url": URLS["mercari"]},
            self.published("mercari"),
        ]
        self.list("mercari")
        self.assertEqual(self.actions(), ["save_draft", "publish"])

    def test_a_form_with_its_own_photos_sends_them_and_every_other_form_keeps_the_item_photos(self):
        repo = ConversationRepo(self.db)
        [front] = repo.get_photos(self.conv.id)
        back = listing_persistence.add_photo(repo.db, self.conv.id, "back.jpg", "back.jpg", "image/jpeg", 1)
        collage = listing_persistence.add_photo(repo.db, self.conv.id, "collage.jpg", "collage.jpg", "image/jpeg", 1, marketplace="mercari")
        listing_persistence.add_photo(repo.db, self.conv.id, "stock.jpg", "stock.jpg", "image/jpeg", 1, marketplace="depop")
        self.rev = listing_persistence.save_revision(self.db,
            self.conv.id, {**LISTING, "marketplace_photos": {"mercari": [collage.id, back.id]}}, "user_form")
        sent = {}
        for platform in URLS:
            self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": URLS[platform]}
            response = self.client.post(f"{self.base}/{platform}/draft", json={"revision_id": self.rev.id})
            self.assertEqual(response.status_code, 200, response.text)
            self.run_jobs()
            sent[platform] = [photo["id"] for photo in self.bridge.call_args.args[2]["photos"]]
            self.assertEqual(self.record(platform)["photo_ids"], sent[platform])
        # Mercari's pick, in its order; Depop's own photo is not ticked, so Depop gets the item's.
        self.assertEqual(sent, {"mercari": [collage.id, back.id], "depop": [front.id, back.id]})

    def test_draft_route_refuses_publish_parameters_unsupported_markets_and_live_items(self):
        response = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(response.status_code, 422)
        for market in ["facebook", "unknown_market"]:
            response = self.client.post(f"{self.base}/{market}/draft", json={"revision_id": self.rev.id})
            self.assertEqual(response.status_code, 422, response.text)
        MarketplaceListingRepo(self.db).save(self.conv.id, "mercari", {"state": "published", "listing_id": "m123456789",
                                                                     "listing_url": LIVE["mercari"]})
        response = self.client.post(f"{self.base}/mercari/draft", json={"revision_id": self.rev.id})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.launched, [])

    def test_poshmark_saves_and_reuses_a_private_draft_without_publication(self):
        draft = "https://poshmark.com/edit-listing/6adc371572e62463d992ffef"
        self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": draft}
        for _ in range(2):
            response = self.client.post(f"{self.base}/poshmark/draft", json={"revision_id": self.rev.id})
            self.assertEqual(response.status_code, 200, response.text)
            self.run_jobs()
            self.assertEqual(self.record("poshmark")["state"], "draft")
            payload = self.bridge.call_args.args[2]
            self.assertEqual((payload["action"], payload["confirm"]), ("save_draft", False))
        self.assertEqual(self.actions(), ["save_draft", "save_draft"])
        self.assertEqual(payload["draft_url"], draft)
        context = native._result_context(self.db, self.conv.id, "poshmark", self.record("poshmark"), LISTING, draft)
        self.assertEqual(context["listing"], LISTING)
        self.assertEqual(context["expected_photo_count"], 1)
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        self.assertEqual(self.list("poshmark")["state"], "published")
        self.assertEqual(self.actions(), ["save_draft", "save_draft", "list"])
        self.assertEqual(self.bridge.call_args.args[2]["draft_url"], draft)

    def test_poshmark_draft_links_reject_other_hosts_queries_and_invalid_ids(self):
        draft = "https://poshmark.com/edit-listing/6adc371572e62463d992ffef"
        self.assertEqual(native.draft_url("poshmark", draft), draft)
        for bad in [draft + "?publish=true", draft + "#publish", draft.replace("poshmark.com", "evil.example"), draft[:-1]]:
            with self.subTest(url=bad), self.assertRaises(native.NativeMarketplaceError):
                native.draft_url("poshmark", bad)

    def test_recovery_keeps_category_size_shipping_and_the_sent_photo_count(self):
        for market in ("mercari", "depop", "poshmark"):
            context = native._result_context(self.db, self.conv.id, market,
                                            {"action": "save_draft", "previous_revision_id": "older", "photo_ids": ["sent-1", "sent-2"]},
                                            LISTING, None)
            self.assertEqual(context["listing"], LISTING)
            self.assertEqual(context["expected_photo_count"], 2)

    def test_list_saves_then_publishes_in_one_job_that_holds_the_slot(self):
        import gc
        from sqlalchemy import event

        # An unclosed observer session can be collected during the worker's
        # next flush and roll back their shared StaticPool connection.
        def collect_during_flush(session, context):
            gc.collect()

        event.listen(self.sessions.class_, "after_flush", collect_during_flush)
        self.addCleanup(event.remove, self.sessions.class_, "after_flush", collect_during_flush)
        for platform in URLS:
            seen = []

            async def bridge(job, kind, payload, timeout=None, platform=platform, seen=seen):
                with self.sessions() as observer:
                    seen.append([j.id for j in JobRepo(observer).get_running()])
                return ({"ok": True, "state": "draft", "draft_url": URLS[platform]} if payload["action"] == "save_draft"
                        else self.published(platform))

            self.bridge.side_effect = bridge
            self.bridge.reset_mock()
            record = self.list(platform)
            self.assertEqual(record["state"], "published", record)
            self.assertEqual(self.actions(), ["save_draft", "publish"])
            calls = self.bridge.call_args_list
            self.assertFalse(calls[0].args[2]["confirm"])
            self.assertTrue(calls[1].args[2]["confirm"])
            self.assertEqual(calls[1].args[2]["draft_url"], URLS[platform])
            # Each step is journaled under its own id, and one job held the slot for both.
            self.assertEqual(calls[0].args[2]["operation_id"], record["job_id"])
            self.assertEqual(calls[1].args[2]["operation_id"], f"{record['job_id']}-2")
            self.assertEqual(seen, [[record["job_id"]], [record["job_id"]]])
            self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "completed")
            self.assertFalse(job_is_live(self.db, record["job_id"]))

    def test_list_does_not_publish_when_preparation_fails(self):
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Mercari needs a brand.", "error_code": "missing_fields",
                                    "fields": [{"field": "brand", "message": "Mercari needs a brand."}],
                                    "marketplace_message": "Brand is required"}
        record = self.list()
        self.assertEqual(record["state"], "not_saved")
        self.assertEqual(self.actions(), ["save_draft"])
        # A definite refusal says why, without the "unconfirmed" warning.
        self.assertEqual(record["error"], "Mercari needs a brand.")
        self.assertEqual((record["error_code"], record["marketplace_message"]), ("missing_fields", "Brand is required"))
        self.assertEqual(record["error_fields"], [{"field": "brand", "message": "Mercari needs a brand."}])
        self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "failed")
        events = [event.payload for event in JobRepo(self.db).get_events(record["job_id"]) if event.event_type == "native_result"]
        self.assertEqual(events[-1]["marketplace_message"], "Brand is required")

    def test_list_finishes_the_current_draft_without_creating_another(self):
        self.bridge.side_effect = [{"ok": False, "safe_to_retry": True, "draft_url": URLS["depop"], "error": "Finish photos."}]
        self.list("depop")
        self.assertEqual(self.record("depop")["state"], "draft")
        MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {"revision_id": self.rev.id, "error": None})
        self.bridge.reset_mock()
        self.bridge.side_effect = [self.published("depop")]
        self.assertEqual(self.list("depop")["state"], "published")
        self.assertEqual(self.actions(), ["publish"])
        self.assertEqual(self.bridge.call_args.args[2]["draft_url"], URLS["depop"])

    def test_list_requires_confirmation_before_any_write(self):
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id}).status_code, 400)
        self.bridge.assert_not_called()
        self.assertFalse(self.launched)

    def test_publish_still_requires_the_explicit_list_endpoint(self):
        self.assertIn(self.client.post(f"{self.base}/mercari/publish", json={"revision_id": self.rev.id, "confirm": True}).status_code, {404, 405})

    def test_list_refuses_an_existing_imported_listing_before_preparing_or_writing(self):
        self.conv.notes = merge_notes(self.conv.notes, {"vendooListingUrls": {"mercari": LIVE["mercari"]}})
        self.db.commit()
        with patch.object(native, "_preflight", new_callable=AsyncMock) as prepare:
            response = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("already listed", response.text)
        prepare.assert_not_called()
        self.bridge.assert_not_called()
        self.assertFalse(self.launched)
        self.assertFalse(JobRepo(self.db).list_by_conversation(self.conv.id))

    def test_list_again_after_a_confirmed_delist(self):
        self.bridge.side_effect = [{"ok": True, "state": "draft", "draft_url": URLS["mercari"]}, self.published("mercari")]
        self.assertEqual(self.list("mercari")["state"], "published")
        # The seller delisted it; Delist records the delete beside the listing.
        self.conv.notes = merge_notes(self.conv.notes, {"marketplaceDelists": {"mercari": {
            "state": "deleted", "listing_id": "m123456789", "url": LIVE["mercari"], "at": "2026-11-05T10:00:00+00:00"}}})
        self.db.commit()
        self.bridge.reset_mock()
        self.bridge.side_effect = [{"ok": True, "state": "draft", "draft_url": URLS["mercari"]},
                                   {**self.published("mercari"), "listing_url": "https://www.mercari.com/us/item/m987654321/"}]
        self.assertEqual(self.list("mercari")["state"], "published")
        self.assertEqual(self.actions(), ["save_draft", "publish"])

    def publish_poshmark_after_stale_seller_link_and_delist(self):
        old_id = "a" * 24
        old_url = f"https://poshmark.com/listing/fixture-old-{old_id}"
        self.conv.notes = merge_notes(self.conv.notes, {
            "marketplaceMarks": {"poshmark": {
                "state": "listed", "listing_id": old_id, "url": old_url,
                "at": "2026-10-01T10:00:00+00:00"}},
            "marketplaceDelists": {"poshmark": {
                "state": "deleted", "listing_id": old_id, "url": old_url,
                "at": "2026-10-02T10:00:00+00:00"}},
        })
        self.db.commit()
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        with patch("socket.socket.connect", side_effect=AssertionError("No fixture marketplace sockets")):
            published = self.list("poshmark")
        self.assertEqual(published["state"], "published")
        self.assertEqual(published["listing_url"], POSH_URL)

    def test_new_poshmark_receipt_is_live_despite_old_seller_link_and_delist(self):
        self.publish_poshmark_after_stale_seller_link_and_delist()
        live = self.client.get(f"/api/conversations/{self.conv.id}/marketplace-listings").json()["poshmark"]
        self.assertEqual(live["state"], "live")
        self.assertEqual(live["url"], POSH_URL)
        self.assertEqual(live["listing_id"], "6adc371572e62463d992ffef")

    def test_new_poshmark_receipt_blocks_another_list_despite_old_delist(self):
        self.publish_poshmark_after_stale_seller_link_and_delist()
        self.bridge.reset_mock()
        refused = self.client.post(f"{self.base}/poshmark/list", json={
            "revision_id": self.rev.id, "confirm": True})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("already listed", refused.json()["detail"])
        self.bridge.assert_not_called()
        self.assertFalse(self.launched)

    def test_a_real_old_poshmark_delist_still_allows_the_seller_to_list_again(self):
        old_id = "6adc371572e62463d992ffef"
        self.conv.notes = merge_notes(self.conv.notes, {
            "marketplaceMarks": {"poshmark": {"state": "listed", "listing_id": old_id, "url": POSH_URL}},
            "marketplaceDelists": {"poshmark": {"state": "deleted", "listing_id": old_id, "url": POSH_URL}},
        })
        self.db.commit()
        MarketplaceListingRepo(self.db).save(self.conv.id, "poshmark", {"state": "published", "listing_url": POSH_URL})
        self.bridge.return_value = {"ok": True, "state": "published",
                                   "listing_url": "https://poshmark.com/listing/fixture-new-bbbbbbbbbbbbbbbbbbbbbbbb"}
        with patch("socket.socket.connect", side_effect=AssertionError("No fixture marketplace sockets")):
            published = self.list("poshmark")
        self.assertEqual(published["state"], "published")
        self.assertEqual(self.actions(), ["list"])
        self.assertEqual(self.bridge.call_count, 1)

    def test_an_unconfirmed_action_stays_fenced_with_a_stale_delist(self):
        self.publish_poshmark_after_stale_seller_link_and_delist()
        MarketplaceListingRepo(self.db).save(self.conv.id, "poshmark", {"state": "unknown", "action": "update"})
        self.bridge.reset_mock()
        response = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.bridge.assert_not_called()
        self.assertEqual(self.status()["poshmark"]["state"], "unknown")

    def test_delisting_the_new_listing_does_not_revive_the_old_seller_link(self):
        import csv
        from io import StringIO

        self.publish_poshmark_after_stale_seller_link_and_delist()
        self.bridge.reset_mock(return_value=True)
        self.bridge.side_effect = [{"ok": True, "state": "deleted"}]
        response = self.client.post(f"/api/conversations/{self.conv.id}/marketplace-listings/delist",
                                    json={"marketplaces": ["poshmark"], "confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.bridge.call_args.args[2]["listing_id"], "6adc371572e62463d992ffef")
        self.assertEqual((response.json()["poshmark"]["state"], response.json()["poshmark"]["listing_id"]),
                         ("deleted", "6adc371572e62463d992ffef"))
        # The delist wrote through its own sessions; read what a new request would see.
        self.db.expire_all()
        detail = self.client.get(f"/api/conversations/{self.conv.id}/marketplace-listings").json()["poshmark"]
        self.assertEqual((detail["state"], detail["listing_id"]), ("deleted", "6adc371572e62463d992ffef"))
        export = self.client.get("/api/sales-ledger/export/inventory.csv")
        row = next(csv.DictReader(StringIO(export.content.decode("utf-8-sig"))))
        self.assertEqual(row["saved_poshmark_url"], "")

        third = "https://poshmark.com/listing/fixture-third-cccccccccccccccccccccccc"
        self.bridge.reset_mock(side_effect=True)
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": third}
        with patch("socket.socket.connect", side_effect=AssertionError("No fixture marketplace sockets")):
            published = self.list("poshmark")
        self.assertEqual((published["state"], published["listing_url"]), ("published", third))
        self.assertEqual(self.bridge.call_count, 1)
        live = self.client.get(f"/api/conversations/{self.conv.id}/marketplace-listings").json()["poshmark"]
        self.assertEqual((live["state"], live["url"]), ("live", third))
        self.bridge.reset_mock()
        again = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(again.status_code, 409, again.text)
        self.bridge.assert_not_called()

    def delist_new_poshmark(self):
        response = self.client.post(f"/api/conversations/{self.conv.id}/marketplace-listings/delist",
                                    json={"marketplaces": ["poshmark"], "confirm": True})
        self.db.expire_all()
        return response

    def assert_new_poshmark_failed_and_fenced(self, response):
        new_id = "6adc371572e62463d992ffef"
        for entry in (response.json()["poshmark"],
                      self.client.get(f"/api/conversations/{self.conv.id}/marketplace-listings").json()["poshmark"]):
            self.assertEqual((entry["state"], entry["listing_id"], entry["url"]), ("failed", new_id, POSH_URL))
            self.assertTrue(entry["error"])
        self.bridge.reset_mock(return_value=True, side_effect=True)
        refused = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.bridge.assert_not_called()
        # A retry deletes the new listing, never the long-deleted old one.
        self.bridge.side_effect = [{"ok": True, "state": "deleted"}]
        retried = self.delist_new_poshmark()
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(self.bridge.call_args.args[2]["listing_id"], new_id)
        self.assertEqual((retried.json()["poshmark"]["state"], retried.json()["poshmark"]["listing_id"]), ("deleted", new_id))

    def test_a_refused_delist_of_the_new_listing_keeps_it_and_its_fence(self):
        self.publish_poshmark_after_stale_seller_link_and_delist()
        self.bridge.reset_mock(return_value=True)
        self.bridge.side_effect = [{"ok": False, "error": "Poshmark did not delete the listing."}]
        response = self.delist_new_poshmark()
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_new_poshmark_failed_and_fenced(response)

    def test_a_delist_of_the_new_listing_that_raises_keeps_it_and_its_fence(self):
        self.publish_poshmark_after_stale_seller_link_and_delist()
        self.bridge.reset_mock(return_value=True)
        self.bridge.side_effect = RuntimeError("Chrome stopped answering")
        response = self.delist_new_poshmark()
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_new_poshmark_failed_and_fenced(response)

    def test_a_read_during_the_delist_shows_the_new_listing_being_deleted(self):
        from anteroom.services import marketplace_delist

        self.publish_poshmark_after_stale_seller_link_and_delist()
        seen = {}

        def read():
            with self.sessions() as db:
                seen.update(marketplace_delist.listings(db, self.conv.id)["poshmark"])

        async def delete_while_reading(*_args, **_kwargs):
            # Another request's read, off the event loop like the app's own.
            await asyncio.to_thread(read)
            return {"ok": True, "state": "deleted"}

        self.bridge.reset_mock(return_value=True)
        self.bridge.side_effect = delete_while_reading
        self.assertEqual(self.delist_new_poshmark().status_code, 200)
        self.assertEqual((seen["state"], seen["listing_id"], seen["url"]), ("deleting", "6adc371572e62463d992ffef", POSH_URL))

    def test_deleting_a_seller_linked_listing_keeps_a_different_live_anteroom_listing(self):
        # Anteroom's own Poshmark listing is live.
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        self.assertEqual(self.list("poshmark")["state"], "published")
        # The seller links a duplicate listing and delists it.
        duplicate_id = "b" * 24
        marked = self.client.post(f"/api/conversations/{self.conv.id}/marketplace-listings/poshmark/mark",
                                  json={"state": "listed", "url": f"https://poshmark.com/listing/dup-{duplicate_id}"})
        self.assertEqual(marked.status_code, 200, marked.text)
        self.bridge.reset_mock(return_value=True)
        self.bridge.side_effect = [{"ok": True, "state": "deleted"}]
        response = self.delist_new_poshmark()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.bridge.call_args.args[2]["listing_id"], duplicate_id)
        # Only the duplicate was deleted: Anteroom's own listing is still live.
        record = self.record("poshmark")
        self.assertEqual((record.get("state"), record.get("listing_url")), ("published", POSH_URL))

    def test_a_confirmed_delist_leaves_the_record_ready_to_list_again(self):
        self.bridge.side_effect = [{"ok": True, "state": "draft", "draft_url": URLS["mercari"]}, self.published("mercari")]
        self.assertEqual(self.list("mercari")["state"], "published")
        self.bridge.reset_mock()
        self.bridge.side_effect = [{"ok": True, "state": "deleted"}]
        response = self.client.post(f"/api/conversations/{self.conv.id}/marketplace-listings/delist",
                                    json={"marketplaces": ["mercari"], "confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["mercari"]["state"], "deleted")
        record = self.record("mercari")
        self.assertNotIn(record["state"], {"published", "pending_review"})
        self.assertIsNone(record.get("listing_url"))
        self.assertEqual(self.status()["mercari"]["state"], record["state"])

    def test_list_refuses_an_item_sold_on_another_marketplace(self):
        self.conv.notes = merge_notes(self.conv.notes, {"marketplaceMarks": {"depop": {"state": "sold"}}})
        self.db.commit()
        with patch.object(native, "_preflight", new_callable=AsyncMock) as prepare:
            response = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("sold", response.text)
        prepare.assert_not_called()
        self.bridge.assert_not_called()
        self.assertFalse(self.launched)

    def test_an_imported_listing_marked_not_listed_can_be_listed_again(self):
        self.conv.notes = merge_notes(self.conv.notes, {
            "vendooListingUrls": {"mercari": LIVE["mercari"]},
            "marketplaceMarks": {"mercari": {"state": "not_listed", "listing_id": "m123456789"}},
        })
        self.db.commit()
        self.bridge.side_effect = [{"ok": True, "state": "draft", "draft_url": URLS["mercari"]}, self.published("mercari")]
        self.assertEqual(self.list("mercari")["state"], "published")

    def test_poshmark_lists_in_one_step_and_updates_the_same_listing(self):
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        record = self.list("poshmark")
        self.assertEqual((record.get("state"), record.get("listing_url")), ("published", POSH_URL))
        self.assertEqual(self.actions(), ["list"])
        self.assertEqual(self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        revised = listing_persistence.save_revision(self.db, self.conv.id, {**LISTING, "title": "Updated tee"}, "user")
        self.rev = revised
        self.assertEqual(self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id}).status_code, 400)
        self.bridge.reset_mock()
        response = self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id, "confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.run_jobs()
        payload = self.bridge.call_args.args[2]
        self.assertEqual((payload["action"], payload["listing_id"], payload["confirm"]), ("update", "6adc371572e62463d992ffef", True))
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Poshmark needs a size.", "error_code": "rejected"}
        self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id, "confirm": True})
        self.run_jobs()
        refused = self.record("poshmark")
        self.assertEqual(refused["state"], "published")
        self.assertIn("size", refused["error"])
        mercari = self.client.post(f"{self.base}/mercari/update", json={"revision_id": revised.id, "confirm": True})
        self.assertEqual(mercari.status_code, 409)
        self.assertIn("not live on Mercari", mercari.text)

    def test_a_confirmed_list_or_update_records_the_price_the_marketplace_got(self):
        # The seller set a Poshmark price apart from the item price.
        priced = {**LISTING, "price": 12, "poshmark_specifics": {"poshmarkPrice": 15},
                  "seller_fields": ["poshmark_specifics.poshmarkPrice"]}
        self.rev = listing_persistence.save_revision(self.db, self.conv.id, priced, "user")
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        self.list("poshmark")
        self.assertEqual(self.record("poshmark")["listed_price"], 15)
        self.assertEqual(MarketplaceListingRepo(self.db).summaries(self.conv.id)[self.conv.id]["poshmark"]["price"], 15)

        revised = listing_persistence.save_revision(self.db, self.conv.id, {**priced, "poshmark_specifics": {"poshmarkPrice": 18}}, "user")
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Poshmark needs a size.", "error_code": "rejected"}
        self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id, "confirm": True})
        self.run_jobs()
        # A refused update leaves the live listing, and its price, as they were.
        self.assertEqual(self.record("poshmark")["listed_price"], 15)

        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id, "confirm": True})
        self.run_jobs()
        self.assertEqual(self.record("poshmark")["listed_price"], 18)

        # A listing the seller links by hand has a price Anteroom never saw.
        from anteroom.services.marketplace_delist import mark

        mark(self.db, self.conv.id, "poshmark", "not_listed")
        self.assertNotIn("listed_price", self.record("poshmark"))
        MarketplaceListingRepo(self.db).save(self.conv.id, "poshmark", {"listed_price": 15})
        mark(self.db, self.conv.id, "poshmark", "listed", POSH_URL)
        record = self.record("poshmark")
        self.assertEqual(record["state"], "published")
        self.assertNotIn("listed_price", record)

    def test_a_confirmed_list_records_when_it_went_up_and_an_update_keeps_it(self):
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        before = datetime.now(UTC)
        self.list("poshmark")
        listed = datetime.fromisoformat(self.record("poshmark")["listed_at"])
        self.assertGreaterEqual(listed, before - timedelta(seconds=1))
        self.assertEqual(MarketplaceListingRepo(self.db).summaries(self.conv.id)[self.conv.id]["poshmark"]["listed_at"],
                         listed.isoformat())

        revised = listing_persistence.save_revision(self.db, self.conv.id, {**LISTING, "title": "Updated tee"}, "user")
        self.client.post(f"{self.base}/poshmark/update", json={"revision_id": revised.id, "confirm": True})
        self.run_jobs()
        # An Update changes the live listing; it does not put up a new one.
        self.assertEqual(datetime.fromisoformat(self.record("poshmark")["listed_at"]), listed)

    def live_on(self, platform, listing_id, url, **record):
        MarketplaceListingRepo(self.db).save(self.conv.id, platform, {
            "state": "published", "listing_id": listing_id, "listing_url": url, "revision_id": self.rev.id, **record})
        self.rev = listing_persistence.save_revision(self.db, self.conv.id, {**LISTING, "title": "Updated tee"}, "user")

    def update(self, platform):
        self.enterContext(patch.object(native.direct_preflight, "gaps", return_value=[]))
        response = self.client.post(f"{self.base}/{platform}/update", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.run_jobs()
        return self.record(platform)



    def test_a_live_update_answer_naming_another_listing_is_not_trusted(self):
        self.live_on("mercari", "m123456789", LIVE["mercari"])
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": "https://www.mercari.com/us/item/m999999999/"}
        updated = self.update("mercari")
        self.assertNotEqual(updated["state"], "published")
        self.assertEqual(self.actions(), ["update"])

    def test_a_refused_poshmark_listing_can_be_tried_again(self):
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Poshmark needs a size.", "error_code": "rejected"}
        self.assertEqual(self.list("poshmark")["state"], "not_saved")
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        self.assertEqual(self.list("poshmark")["state"], "published")





    def test_etsy_uses_the_listing_package(self):
        self.assertEqual(native.listing_package({"weight_lb": 1, "weight_oz": 4, "package_dimensions_in": "13x10x3"}),
                         {"item_weight": 20.0, "item_length": 13.0, "item_width": 10.0, "item_height": 3.0,
                          "item_weight_unit": "oz", "item_dimensions_unit": "in"})
        self.assertIsNone(native.listing_package({"weight_oz": 0, "package_dimensions_in": "13x10x3"}))




    def test_a_listed_poshmark_post_keeps_the_extensions_warning(self):
        outcome = native._outcome("poshmark", "list", {}, {
            "ok": True, "state": "published", "listing_url": POSH_URL, "warning": "Poshmark left Smart Pricing on.",
        })
        self.assertEqual((outcome["state"], outcome["error"], outcome["error_code"]), ("published", "Poshmark left Smart Pricing on.", None))

    def test_a_depop_category_change_keeps_the_rewritten_slug(self):
        old = "https://www.depop.com/products/vassarette-womens-tan-vest/"
        new = "https://www.depop.com/products/vassarette-womens-tan-sweater/"
        outcome = native._outcome("depop", "update", {
            "state": "published", "listing_id": "vassarette-womens-tan-vest", "listing_url": old,
        }, {"ok": True, "state": "published", "listing_url": new, "previous_slug": "vassarette-womens-tan-vest"})
        self.assertEqual(outcome["listing_id"], "vassarette-womens-tan-sweater")
        self.assertEqual(outcome["listing_url"], new)
        with self.assertRaises(native.NativeMarketplaceError):
            native._outcome("depop", "update", {
                "state": "published", "listing_id": "vassarette-womens-tan-vest", "listing_url": old,
            }, {"ok": True, "state": "published", "listing_url": new, "previous_slug": "someone-elses-vest"})

    def test_a_depop_publish_keeps_the_public_product_link(self):
        url = "https://www.depop.com/products/seller-columbia-xl/"
        outcome = native._outcome("depop", "publish", {"state": "draft", "draft_url": URLS["depop"]}, {
            "ok": True, "state": "published", "draft_url": URLS["depop"], "listing_url": url,
        })
        self.assertEqual(outcome["state"], "published")
        self.assertEqual(outcome["listing_url"], url)

    def test_a_boosted_depop_list_turns_boost_on_with_one_edit_of_the_new_listing(self):
        url = "https://www.depop.com/products/seller-boosted-tee/"

        def answer(_job, kind, payload, **_kwargs):
            if kind != "marketplace.execute":
                return {"ok": True}
            if payload["action"] == "save_draft":
                return {"ok": True, "state": "draft", "draft_url": URLS["depop"]}
            return {"ok": True, "state": "published", "draft_url": URLS["depop"], "listing_url": url}

        self.bridge.side_effect = answer
        listing = {**LISTING, "depop_specifics": {**LISTING["depop_specifics"], "boost": True}}
        self.rev = listing_persistence.save_revision(self.db, self.conv.id, listing, "seller")
        self.list("depop")
        # A plain List stops at publish (test_list_saves_then_publishes_in_one_job_that_holds_the_slot).
        self.assertEqual(self.actions(), ["save_draft", "publish", "update"])
        update = [call.args[2] for call in self.bridge.call_args_list
                  if call.args[1] == "marketplace.execute" and call.args[2]["action"] == "update"][0]
        self.assertIs(update["confirm"], True)
        self.assertEqual(update["listing_id"], "seller-boosted-tee")
        self.assertIs(update["listing"]["depop_specifics"]["boost"], True)

    def test_a_deleted_depop_draft_is_forgotten_and_a_replacement_is_kept(self):
        old = URLS["depop"]
        new = "https://www.depop.com/sellinghub/drafts/edit/newdraft1/"
        record = {"state": "draft", "draft_url": old, "revision_id": "current"}
        gone = native._outcome("depop", "publish", record, {
            "ok": False, "safe_to_retry": True, "error": "Depop refused the request (HTTP 404).",
            "marketplace_message": "Could not find draft with id: [hidden]", "error_code": "rejected",
            "draft_url": old,
        })
        self.assertIsNone(gone["draft_url"])
        self.assertEqual(gone["state"], "not_saved")
        kept = native._outcome("depop", "save_draft", record, {
            "ok": True, "state": "draft", "draft_url": new, "draft_replaced": True,
        })
        self.assertEqual(kept["draft_url"], new)
        with self.assertRaises(native.NativeMarketplaceError):
            native._outcome("depop", "save_draft", record, {"ok": True, "state": "draft", "draft_url": new})
        refused = native._outcome("depop", "save_draft", record, {
            "ok": False, "safe_to_retry": True, "draft_replaced": True, "draft_url": new,
            "error": "Depop refused the request (HTTP 400).", "error_code": "rejected",
            "marketplace_message": "parcel_size is required",
        })
        self.assertEqual(refused["draft_url"], new)
        self.assertEqual(refused["state"], "draft")
        self.assertIn("HTTP 400", refused["error"])
        self.assertEqual(refused["marketplace_message"], "parcel_size is required")
        unrelated = native._outcome("depop", "save_draft", record, {
            "ok": False, "safe_to_retry": True, "draft_url": new,
            "error": "Depop refused the request (HTTP 400).", "error_code": "rejected",
        })
        self.assertNotIn("draft_url", unrelated)
        self.assertEqual(unrelated["state"], "draft")
        self.assertIn("HTTP 400", unrelated["error"])
        self.assertNotEqual(unrelated["state"], "unknown")

    def test_listing_again_replaces_a_deleted_depop_draft_in_the_same_list(self):
        self.bridge.return_value = {"ok": True, "state": "draft", "draft_url": URLS["depop"]}
        saved = self.client.post(f"{self.base}/depop/draft", json={"revision_id": self.rev.id})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.run_jobs()
        self.bridge.reset_mock()
        new = "https://www.depop.com/sellinghub/drafts/edit/newdraft1/"
        self.bridge.side_effect = [
            {"ok": False, "safe_to_retry": True, "error": "Depop refused the request (HTTP 404).",
             "marketplace_message": "Could not find draft with id: [hidden]", "error_code": "rejected"},
            {"ok": True, "state": "draft", "draft_url": new},
            {"ok": True, "state": "published", "draft_url": new, "listing_url": LIVE["depop"]},
        ]
        record = self.list("depop")
        self.assertEqual(record["state"], "published")
        self.assertEqual(record["draft_url"], new)
        actions = [call.args[2]["action"] for call in self.bridge.call_args_list]
        self.assertEqual(actions, ["publish", "save_draft", "publish"])
        self.assertEqual(self.bridge.call_args_list[0].args[2]["draft_url"], URLS["depop"])
        self.assertIsNone(self.bridge.call_args_list[1].args[2]["draft_url"])

    def test_listing_links_are_validated_for_every_marketplace(self):
        self.bridge.side_effect = [{"ok": True, "state": "draft", "draft_url": URLS["mercari"]},
                                   {"ok": True, "state": "published", "draft_url": URLS["mercari"], "listing_url": "https://www.mercari.com/us/search/"}]
        record = self.list()
        self.assertEqual(record["state"], "unknown")
        self.assertIn("invalid listing link", record["error"])
        self.bridge.side_effect = None
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": "https://poshmark.com/closet/someone"}
        self.assertEqual(self.list("poshmark")["state"], "unknown")
        self.assertEqual(native.listing_url("mercari", "https://www.mercari.com/us/item/m123456789/?ref=x"),
                         "https://www.mercari.com/us/item/m123456789/")

    # --- lost replies, check, reset ------------------------------------------------

    def test_a_late_chrome_reply_keeps_the_slot_until_chrome_reports(self):
        replies = iter([{"finished": False}, {"finished": True, "result": self.published("mercari")}])
        running_during_wait = []

        async def bridge(job, kind, payload, timeout=None):
            if kind == "marketplace.execute":
                if payload["action"] == "save_draft":
                    return {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}
                raise BrowserBridgeError("Chrome did not answer in time.")
            running_during_wait.append([j.current_step for j in JobRepo(self.sessions()).get_running()])
            return next(replies)

        self.bridge.side_effect = bridge
        record = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).json()
        self.run_jobs()
        # Chrome was told to stop the late step, by the id it journals it under.
        cancel = self.manager.send_message.call_args_list[0].args[0]
        self.assertEqual((cancel["type"], cancel["job_id"]), ("job.cancel", f"{record['job_id']}-2"))
        self.assertEqual(running_during_wait, [["native_mercari_publish"], ["native_mercari_publish"]])
        self.assertEqual(self.record("mercari")["state"], "published")
        # Only now is the direct-action slot free.
        self.assertFalse(JobRepo(self.db).get_running())

    def test_no_journal_is_surfaced_and_the_seller_can_reset(self):
        async def bridge(job, kind, payload, timeout=None):
            if kind == "marketplace.execute":
                raise BrowserBridgeError("lost")
            return {"finished": False}

        self.bridge.side_effect = bridge
        with patch.object(native, "SETTLE_DEADLINE_S", 0):
            record = self.list()
        self.assertEqual((record["state"], record["error_code"]), ("unknown", "uncertain"))
        self.assertIn("Chrome has not said whether this Mercari action finished", record["error"])
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        checked = self.client.post(f"{self.base}/mercari/check")
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual(checked.json()["state"], "unknown")
        self.assertIn("I checked it myself", checked.json()["error"])
        self.assertEqual(self.client.post(f"{self.base}/mercari/reset", json={}).status_code, 400)
        writes = self.actions()
        reset = self.client.post(f"{self.base}/mercari/reset", json={"confirm": True})
        self.assertEqual(reset.json()["state"], "not_saved", reset.text)
        self.assertIsNone(reset.json().get("error"))
        self.assertEqual(self.actions(), writes)
        events = [event for event in JobRepo(self.db).get_events(record["job_id"]) if event.event_type == "native_reset"]
        self.assertEqual(events[0].payload, {"marketplace": "mercari", "from": "unknown", "to": "not_saved"})
        self.assertEqual(self.client.post(f"{self.base}/mercari/reset", json={"confirm": True}).status_code, 409)
        self.bridge.side_effect = [{"ok": True, "state": "draft", "draft_url": URLS["mercari"]}, self.published("mercari")]
        self.assertEqual(self.list()["state"], "published")

    def test_check_reads_the_journal_and_verifies_the_job(self):
        self.bridge.side_effect = BrowserBridgeError("lost")
        with patch.object(native, "SETTLE_DEADLINE_S", 0):
            record = self.list("poshmark")
        self.assertEqual(record["state"], "unknown")
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "published", "listing_url": POSH_URL}}
        checked = self.client.post(f"{self.base}/poshmark/check").json()
        self.assertEqual(checked["state"], "published")
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")
        self.assertEqual(self.bridge.call_args.args[2]["context"]["action"], "list")
        self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "completed")
        jobs = self.client.get(f"/api/jobs?conversation_id={self.conv.id}").json()
        self.assertTrue(all(job["mode"] == "native_marketplace" for job in jobs))




    def test_a_check_that_answers_after_the_seller_added_the_link_keeps_the_link(self):
        # Production, 2026-11-18: Depop's publish was unconfirmed, a Check was asking
        # Chrome, and the seller pasted the live link before Chrome answered.
        from anteroom.database import in_worker_session

        # Read once: the test session's commits expire self.conv, and reading it again
        # inside a Chrome stand-in would query on the event loop.
        conv_id = self.conv.id
        from anteroom.services.marketplace_delist import mark

        async def publish_lost(job, kind, payload, timeout=None):
            if kind == "marketplace.execute" and payload["action"] == "save_draft":
                return {"ok": True, "state": "draft", "draft_url": URLS["depop"]}
            if kind == "marketplace.execute":
                raise BrowserBridgeError("lost")
            return {"finished": False}

        self.bridge.side_effect = publish_lost
        with patch.object(native, "SETTLE_DEADLINE_S", 0):
            record = self.list("depop")
        self.assertEqual((record["state"], record["action"]), ("unknown", "publish"))
        seller_link = "https://www.depop.com/products/seller-added-link-e2f1/"
        stale = {"ok": False, "safe_to_retry": False, "error": "Depop refused the request (HTTP 404).",
                 "error_code": "uncertain", "marketplace_message": "HTTP 404"}

        other = {"ok": True, "state": "published", "draft_url": record["draft_url"],
                 "listing_url": "https://www.depop.com/products/some-other-listing-1234/"}
        # DGK (c02f59cb2eb7): Mark as listed. Gear for Sports (b2d0b9f27f87): "I checked it
        # myself" first, which rests the record as a draft, then Mark as listed.
        for answer, reset_first in ((stale, False), (other, False), (stale, True)):
            async def bridge(job, kind, payload, timeout=None, answer=answer, reset_first=reset_first):
                if reset_first:
                    rested = await in_worker_session(lambda db: native.reset(conv_id, "depop", db))
                    self.assertEqual(rested["state"], "draft")
                await in_worker_session(mark, conv_id, "depop", "listed", seller_link)
                return {"finished": True, "result": answer}

            MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {"state": "unknown", "listing_url": None,
                                                                         "listing_id": None})
            self.bridge.side_effect = bridge
            checked = self.client.post(f"{self.base}/depop/check")
            self.assertEqual(checked.status_code, 200, checked.text)
            kept = self.record("depop")
            self.assertEqual((kept["state"], kept["listing_url"]), ("published", seller_link))
            self.assertIsNone(kept.get("error"))
            events = [event.event_type for event in JobRepo(self.db).get_events(record["job_id"])]
            self.assertIn("native_check_superseded", events)

        # "I checked it myself" alone, then Chrome confirms the listing: the confirmed
        # answer wins, or the record would stay a draft and the next List would post a second copy.
        async def confirmed_after_reset(job, kind, payload, timeout=None):
            await in_worker_session(lambda db: native.reset(conv_id, "depop", db))
            return {"finished": True, "result": {"ok": True, "state": "published", "draft_url": record["draft_url"],
                                                 "listing_url": LIVE["depop"]}}

        # A confirmation for some other draft is not one: the reset stays.
        async def other_draft_after_reset(job, kind, payload, timeout=None):
            await in_worker_session(lambda db: native.reset(conv_id, "depop", db))
            return {"finished": True, "result": {"ok": True, "state": "published", "listing_url": LIVE["depop"],
                                                 "draft_url": "https://www.depop.com/sellinghub/drafts/edit/otherdraft9/"}}

        MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {"state": "unknown", "listing_url": None,
                                                                     "listing_id": None})
        self.bridge.side_effect = other_draft_after_reset
        checked = self.client.post(f"{self.base}/depop/check")
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual(self.record("depop")["state"], "draft")

        MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {"state": "unknown", "listing_url": None,
                                                                     "listing_id": None})
        self.bridge.side_effect = confirmed_after_reset
        checked = self.client.post(f"{self.base}/depop/check")
        self.assertEqual(checked.status_code, 200, checked.text)
        live = self.record("depop")
        self.assertEqual((live["state"], live["listing_url"]), ("published", LIVE["depop"]))

        # "I checked it myself" finished before the Check opened on the resting draft; the
        # seller then marks it listed while Chrome answers. The mark is newer than either answer.
        for answer in (None, other):
            MarketplaceListingRepo(self.db).save(self.conv.id, "depop", {"state": "unknown", "listing_url": None,
                                                                         "listing_id": None})
            from anteroom.services.marketplace_delist import _store as store_note
            store_note(self.db, self.conv.id, "marketplaceMarks", "depop", None)
            self.assertEqual(native.reset(self.conv.id, "depop", self.db)["state"], "draft")

            async def mark_while_checking(job, kind, payload, timeout=None, answer=answer):
                await in_worker_session(mark, conv_id, "depop", "listed", seller_link)
                return {"finished": answer is not None, "result": answer}

            self.bridge.side_effect = mark_while_checking
            checked = self.client.post(f"{self.base}/depop/check")
            self.assertEqual(checked.status_code, 200, checked.text)
            kept = self.record("depop")
            self.assertEqual((kept["state"], kept["listing_url"]), ("published", seller_link))

    def test_a_pasted_draft_link_is_checked_without_repeating_the_write(self):
        self.bridge.side_effect = BrowserBridgeError("lost")
        with patch.object(native, "SETTLE_DEADLINE_S", 0):
            self.list()
        self.bridge.side_effect = None
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}}
        result = self.client.post(f"{self.base}/mercari/check", json={"draft_url": URLS["mercari"]}).json()
        self.assertEqual(result["state"], "draft", result)
        self.assertEqual(self.bridge.call_args.args[2]["context"]["draft_url"], URLS["mercari"])

    def test_cancel_stops_a_running_list_before_it_publishes(self):
        started = []

        async def bridge(job, kind, payload, timeout=None):
            started.append(payload["action"])
            # The seller presses Cancel while the draft saves.
            self.assertEqual(self.client.post(f"{self.base}/mercari/cancel").status_code, 200)
            return {"ok": True, "state": "draft", "draft_url": URLS["mercari"]}

        self.bridge.side_effect = bridge
        response = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).json()
        self.run_jobs()
        self.assertEqual(started, ["save_draft"])
        self.assertEqual(self.record("mercari")["state"], "draft")
        self.assertEqual(JobRepo(self.db).get(response["job_id"]).status, "cancelled")
        sent = self.manager.send_message.call_args.args[0]
        self.assertEqual((sent["type"], sent["job_id"]), ("job.cancel", response["job_id"]))
        self.assertEqual(self.client.post(f"{self.base}/mercari/cancel").status_code, 409)

    def test_cancel_ends_a_list_chrome_never_answers_without_waiting_out_the_deadline(self):
        calls = []

        async def bridge(job, kind, payload, timeout=None):
            calls.append(kind)
            if kind == "marketplace.execute":
                # Chrome went quiet; the seller presses Cancel while Anteroom waits.
                self.assertEqual(self.client.post(f"{self.base}/poshmark/cancel").status_code, 200)
            await asyncio.sleep(3600)

        self.bridge.side_effect = bridge
        with patch.object(native, "CANCEL_POLL_S", 0.01):
            response = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).json()
            started = time.monotonic()
            self.run_jobs()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(calls, ["marketplace.execute"])
        record = self.record("poshmark")
        # Chrome may still have listed it, so the seller is asked to look before listing again.
        self.assertEqual((record["state"], record["error_code"]), ("unknown", "uncertain"))
        self.assertEqual(JobRepo(self.db).get(response["job_id"]).status, "cancelled")

    def test_a_job_waiting_on_chrome_holds_no_database_connection(self):
        opened = []

        def session():
            opened.append(self.sessions())
            return opened[-1]

        held = []

        async def bridge(job, kind, payload, timeout=None):
            # The job's own session is the one opened for it.
            held.append(opened[-1].in_transaction())
            if kind == "marketplace.execute":
                raise BrowserBridgeError("lost")
            return {"finished": True, "result": {"ok": True, "state": "published", "listing_url": POSH_URL}}

        self.bridge.side_effect = bridge
        with patch("anteroom.database.SessionLocal", session):
            self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True})
            self.run_jobs()
        self.assertEqual(held, [False, False])

    def test_chrome_gets_the_listing_without_the_saved_form_contracts_and_each_check_names_its_marketplace(self):
        # Saved form contracts reach megabytes; the Bridge refuses a command over 2 MB.
        listing = {**LISTING, "_native_forms": {"mercari": {"fields": ["x" * 3_000_000]}}}
        revision = listing_persistence.save_revision(self.db, self.conv.id, listing, "model")
        self.bridge.side_effect = [BrowserBridgeError("lost"), {"finished": True, "result": {"ok": True, "state": "published", "listing_url": POSH_URL}}]
        response = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": revision.id, "confirm": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.run_jobs()
        (_, kind, execute), _ = self.bridge.call_args_list[0]
        (_, check_kind, check), _ = self.bridge.call_args_list[1]
        self.assertEqual((kind, check_kind), ("marketplace.execute", "marketplace.result"))
        self.assertFalse("_native_forms" in execute["listing"])
        self.assertFalse("_native_forms" in check["context"]["listing"])
        self.assertLess(len(json.dumps(execute)), 2_000_000)
        self.assertEqual(check["marketplace"], "poshmark")
        self.assertEqual(self.record("poshmark")["state"], "published")

    def test_an_ebay_list_whose_reply_was_lost_is_checked_before_the_job_lets_go(self):
        from anteroom.services import ebay_listings

        async def execute(action, snapshot, photos, binding, record, active, **kwargs):
            await record({"ebay_sku": "ar-x", "ebay_offer_id": "9", "ebay_inventory_written": True})
            return {"ok": False, "safe_to_retry": False, "error": "eBay did not confirm the listing.", "error_code": "uncertain"}

        checked = AsyncMock(return_value={"ok": True, "state": "published", "listing_url": "https://www.ebay.com/itm/123456789012"})
        with patch("anteroom.services.ebay_client.EbayClient.connect", AsyncMock()), \
             patch.object(ebay_listings, "execute", execute), patch.object(ebay_listings, "check", checked):
            record = self.list("ebay")
        self.assertEqual((record["state"], record["listing_url"]), ("published", "https://www.ebay.com/itm/123456789012"))
        self.assertEqual(checked.await_args.args[0]["ebay_offer_id"], "9")
        self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "completed")

    def test_a_refused_ebay_publish_leaves_a_resumable_draft_with_its_ids(self):
        from anteroom.services import ebay_listings

        async def execute(action, snapshot, photos, binding, record, active, **kwargs):
            await record({"ebay_sku": "ar-x", "ebay_offer_id": "9", "ebay_inventory_written": True})
            return {"ok": False, "safe_to_retry": True, "error": "eBay returned HTTP 400.", "error_code": "rejected",
                    "marketplace_message": "25002: Color is missing."}

        with patch("anteroom.services.ebay_client.EbayClient.connect", AsyncMock()), patch.object(ebay_listings, "execute", execute):
            record = self.list("ebay")
            self.assertEqual((record["state"], record["ebay_offer_id"], record["error_code"]), ("draft", "9", "rejected"))
            self.assertEqual(record["marketplace_message"], "25002: Color is missing.")
            # List again is allowed and starts from the same offer.
            self.assertEqual(self.client.post(f"{self.base}/ebay/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 200)

    def test_an_ebay_draft_is_an_unpublished_offer_checked_without_a_link(self):
        from anteroom.services import ebay_listings

        async def execute(action, snapshot, photos, binding, record, active, **kwargs):
            self.assertEqual((action, kwargs["confirm"]), ("save_draft", False))
            await record({"ebay_sku": "ar-x", "ebay_offer_id": "9", "ebay_inventory_written": True})
            return {"ok": True, "state": "draft"}

        checked = AsyncMock(return_value={"ok": True, "state": "draft"})
        with patch("anteroom.services.ebay_client.EbayClient.connect", AsyncMock()), \
             patch.object(ebay_listings, "execute", execute), patch.object(ebay_listings, "check", checked):
            response = self.client.post(f"{self.base}/ebay/draft", json={"revision_id": self.rev.id})
            self.assertEqual(response.status_code, 200, response.text)
            self.run_jobs()
            record = self.status()["ebay"]
            self.assertEqual((record["state"], record.get("draft_url"), record.get("error")), ("draft", None, None))
            self.assertEqual(JobRepo(self.db).get(record["job_id"]).status, "completed")
            self.assertEqual(self.client.post(f"{self.base}/ebay/check", json={}).status_code, 200)
            self.run_jobs()
        # Check compares the revision the draft was saved from.
        self.assertIn(self.rev.listing_json["title"], checked.await_args.args[1]["title"])

    # --- records outlive the editor ---------------------------------------------------

    def test_clear_and_regenerate_keep_records_and_stale_notes_cannot_overwrite_them(self):
        self.bridge.return_value = {"ok": True, "state": "published", "listing_url": POSH_URL}
        record = self.list("poshmark")
        stale = merge_notes(self.conv.notes, {"nativeMarketplaceDrafts": {"poshmark": {"state": "not_saved"}}, "brand": "Levi"})
        self.assertEqual(self.client.patch(f"/api/conversations/{self.conv.id}", json={"notes": stale}).status_code, 200)
        self.assertEqual(self.record("poshmark")["state"], "published")
        for body in ({"keep_inputs": True}, {}):
            response = self.client.post(f"/api/conversations/{self.conv.id}/reset", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.db.expire_all()
            self.assertEqual(self.record("poshmark")["listing_url"], POSH_URL)
            # The job the record names stays, so Check can still read its snapshot.
            self.assertIsNotNone(JobRepo(self.db).get(record["job_id"]))
        self.assertEqual(self.client.get(f"/api/conversations/{self.conv.id}/marketplace-listings").json()["poshmark"]["listing_id"],
                         "6adc371572e62463d992ffef")
        claim_api_job(self.db, record["job_id"])
        MarketplaceListingRepo(self.db).save(self.conv.id, "poshmark", {"state": "updating"})
        try:
            self.assertEqual(self.client.post(f"/api/conversations/{self.conv.id}/reset", json={}).status_code, 409)
        finally:
            release_api_job(record["job_id"])

    def test_deleting_the_item_deletes_its_records(self):
        from anteroom.services.listing_delete import delete_listing

        MarketplaceListingRepo(self.db).save(self.conv.id, "ebay", {"state": "published"})
        delete_listing(self.db, self.conv.id)
        self.assertEqual(MarketplaceListingRepo(self.db).for_conversation(self.conv.id), {})

    def test_a_failed_direct_listing_does_not_relabel_the_item(self):
        self.bridge.return_value = {"ok": False, "safe_to_retry": True, "error": "Poshmark needs a size.", "error_code": "rejected"}
        self.list("poshmark")
        ConversationRepo(self.db).reconcile_job_statuses()
        self.db.refresh(self.conv)
        self.assertNotEqual(self.conv.status, "failed")

    # --- the slot, restarts and reconnects ------------------------------------------------

    def test_a_bridge_from_another_build_runs_actions_it_declares(self):
        # Chrome may run an older or separately loaded copy of the Bridge. What
        # decides is whether it declares the ability, not which build it is.
        self.manager.build = "older-build"
        self.manager.capabilities = frozenset()
        result = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(result.status_code, 409)
        self.assertIn("Update Anteroom Bridge", result.json()["detail"])
        self.assertFalse(JobRepo(self.db).list_all())
        self.bridge.assert_not_called()
        self.manager.capabilities = frozenset({"marketplace.actions"})
        restored = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["running"])

    def test_other_jobs_block_native_actions(self):
        JobRepo(self.db).create(self.conv.id, self.rev.id, LISTING)
        self.assertEqual(self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True}).status_code, 409)
        self.bridge.assert_not_called()

    def test_listing_runs_beside_other_marketplaces_but_not_its_own(self):
        repo = JobRepo(self.db)
        other = ConversationRepo(self.db).create(title="Other item")
        repo.create(other.id, self.rev.id, LISTING, status="dispatched", current_step="native_poshmark_list")
        started = self.client.post(f"{self.base}/mercari/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(started.status_code, 200, started.text)
        refused = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Another Poshmark action", refused.json()["detail"])
        # The app reads this to let the seller's confirmed List wait its turn.
        self.assertEqual(refused.headers.get("X-Anteroom-Reason"), "marketplace_busy")

    def test_only_a_busy_marketplace_is_marked_as_waiting(self):
        JobRepo(self.db).create(self.conv.id, self.rev.id, LISTING)
        refused = self.client.post(f"{self.base}/mercari/update", json={"revision_id": self.rev.id, "confirm": True})
        self.assertEqual(refused.status_code, 409)
        self.assertIsNone(refused.headers.get("X-Anteroom-Reason"))

    def test_delist_still_holds_every_marketplace(self):
        job = JobRepo(self.db).create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_delist")
        self.assertEqual(native.blocking_job(JobRepo(self.db), "mercari").id, job.id)

    def test_restart_marks_cut_off_actions_unconfirmed_and_reconcile_reads_them(self):
        repo = JobRepo(self.db)
        job = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_poshmark_list")
        MarketplaceListingRepo(self.db).save(self.conv.id, "poshmark", {"state": "publishing", "action": "list", "job_id": job.id,
                                                                       "operation_id": job.id})
        fail_stalled_jobs(self.db, now=utcnow() + timedelta(seconds=6))
        self.assertEqual(job.status, "failed")
        self.assertEqual(fail_stalled_jobs(self.db), [])
        self.assertEqual(self.record("poshmark")["state"], "unknown")
        self.bridge.return_value = {"finished": True, "result": {"ok": True, "state": "published", "listing_url": POSH_URL}}
        # Startup reconciles eBay and Etsy only; Chrome's records wait for Chrome.
        self.assertEqual(asyncio.run(native.reconcile(chrome=False)), 0)
        self.assertEqual(asyncio.run(native.reconcile(chrome=True)), 1)
        self.db.expire_all()
        self.assertEqual(self.record("poshmark")["state"], "published")
        self.assertEqual(self.bridge.call_args.args[1], "marketplace.result")
        self.assertFalse(any(call.args[1] == "marketplace.execute" for call in self.bridge.call_args_list))

    def test_the_watchdog_fails_dead_native_jobs_and_leaves_live_ones_alone(self):
        repo = JobRepo(self.db)
        dead = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_save_draft")
        dead.updated_at = utcnow() - timedelta(seconds=10)
        live = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_depop_publish")
        live.updated_at = utcnow() - timedelta(minutes=30)
        self.db.commit()
        MarketplaceListingRepo(self.db).save(self.conv.id, "mercari", {"state": "saving", "job_id": dead.id})
        claim_api_job(self.db, live.id)
        try:
            self.assertEqual(fail_stalled_jobs(self.db), [dead.id])
            self.assertEqual(live.status, "dispatched")
        finally:
            release_api_job(live.id)
        self.assertEqual(self.record("mercari")["state"], "unknown")
        self.assertFalse(any(job.status == "queued" for job in repo.list_by_conversation(self.conv.id)))

    def test_reconnect_keeps_a_native_action_running_in_this_process(self):
        repo = JobRepo(self.db)
        job = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_publish")
        claim_api_job(self.db, job.id)
        try:
            fail_stalled_jobs(self.db, now=utcnow() + timedelta(seconds=6))
            self.assertEqual(job.status, "dispatched")
        finally:
            release_api_job(job.id)

    # --- Two Machines sharing one database ---------------------------------------

    def machines(self):
        a = api_job_lock.JobLeases("machineA", heartbeat=False)
        b = api_job_lock.JobLeases("machineB", heartbeat=False)
        self.addCleanup(lambda: [leases.release(job.id) for job in JobRepo(self.db).list_all() for leases in (a, b)])
        return a, b

    def test_on_the_mac_a_running_job_is_tracked_in_memory_only(self):
        job = JobRepo(self.db).create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_publish")
        self.assertIsNone(api_job_lock.process_leases().owner)
        claim_api_job(self.db, job.id)
        try:
            self.db.refresh(job)
            self.assertEqual((job.lease_owner, job.leased_until), (None, None))
            self.assertTrue(job_is_live(self.db, job.id))
        finally:
            release_api_job(job.id)
        self.assertFalse(job_is_live(self.db, job.id))

    def test_a_watchdog_on_another_machine_leaves_a_running_job_until_its_lease_lapses(self):
        a, b = self.machines()
        repo = JobRepo(self.db)
        job = repo.create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_publish")
        MarketplaceListingRepo(self.db).save(self.conv.id, "mercari", {"state": "publishing", "job_id": job.id})
        a.claim(self.db, job.id)
        # Quiet far past the orphan grace, but A's lease is current: B leaves it running.
        self.assertEqual(fail_stalled_jobs(self.db, now=utcnow() + timedelta(seconds=20), leases=b), [])
        self.db.expire_all()
        self.assertEqual(repo.get(job.id).status, "dispatched")
        self.assertEqual(self.record("mercari")["state"], "publishing")
        with patch.object(api_job_lock, "_process", b):
            self.assertTrue(job_is_live(self.db, job.id))
            # Check, Reset and Mark on B wait for it too.
            self.assertEqual(self.client.post(f"/api/conversations/{self.conv.id}/reset", json={}).status_code, 409)
        # A renews while B sweeps: B's stale view does not fail it.
        a.renew()
        self.assertFalse(b.interrupt(self.db, job.id, "x", utcnow()))
        # A stops renewing. Once its lease lapses, B marks the job interrupted and its result unconfirmed.
        lapsed = utcnow() + api_job_lock.LEASE + timedelta(seconds=1)
        self.assertEqual(fail_stalled_jobs(self.db, now=lapsed, leases=b), [job.id])
        self.db.expire_all()
        failed = repo.get(job.id)
        self.assertEqual(failed.status, "failed")
        self.assertIn("interrupted", failed.last_error)
        record = self.record("mercari")
        self.assertEqual((record["state"], record["error_code"]), ("unknown", "uncertain"))
        # Nothing is retried: no new job, no marketplace call.
        self.assertEqual([j.id for j in repo.list_by_conversation(self.conv.id)], [job.id])
        self.bridge.assert_not_called()
        self.assertEqual(self.launched, [])
        # If A is still alive, its next renewal finds the job gone and stops it at its next safe point.
        self.assertFalse(a.cancel_requested(job.id))
        a.renew()
        self.assertTrue(a.cancel_requested(job.id))

    def test_a_restarted_machine_does_not_wait_out_its_own_old_lease(self):
        a, _b = self.machines()
        job = JobRepo(self.db).create(self.conv.id, self.rev.id, LISTING, status="dispatched", current_step="native_mercari_publish")
        a.claim(self.db, job.id)
        restarted = api_job_lock.JobLeases("machineA", heartbeat=False)
        self.assertEqual(fail_stalled_jobs(self.db, now=utcnow() + timedelta(seconds=6), leases=restarted), [job.id])

    def test_cancel_on_another_machine_stops_the_job_where_it_runs(self):
        a, b = self.machines()
        calls = []

        async def bridge(job, kind, payload, timeout=None):
            calls.append(kind)
            if kind == "marketplace.execute":
                # The seller's Cancel lands on B while A waits on Chrome.
                with patch.object(api_job_lock, "_process", b):
                    self.assertEqual(self.client.post(f"{self.base}/poshmark/cancel").status_code, 200)
                    # A second press does not ask again.
                    self.client.post(f"{self.base}/poshmark/cancel")
                self.assertFalse(a.cancel_requested(job.id))
                # A's next heartbeat reads the request from the job row.
                a.renew()
            await asyncio.sleep(3600)

        self.bridge.side_effect = bridge
        with patch.object(api_job_lock, "_process", a), patch.object(native, "CANCEL_POLL_S", 0.01):
            response = self.client.post(f"{self.base}/poshmark/list", json={"revision_id": self.rev.id, "confirm": True}).json()
            started = time.monotonic()
            self.run_jobs()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(calls, ["marketplace.execute"])
        job = JobRepo(self.db).get(response["job_id"])
        self.assertEqual((job.status, job.cancel_requested, job.lease_owner), ("cancelled", True, "machineA"))
        requested = [e for e in JobRepo(self.db).get_events(job.id) if e.event_type == "native_cancel_requested"]
        self.assertEqual(len(requested), 1)
        sent = self.manager.send_message.call_args.args[0]
        self.assertEqual(sent["type"], "job.cancel")
        record = self.record("poshmark")
        self.assertEqual((record["state"], record["error_code"]), ("unknown", "uncertain"))


class MigrationFromNotesTest(unittest.TestCase):
    def test_records_move_out_of_notes(self):
        import importlib.util
        from pathlib import Path

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        import anteroom

        path = next((Path(anteroom.__file__).parent / "migrations" / "versions").glob("292cc5d3b9a5_*.py"))
        spec = importlib.util.spec_from_file_location("migration_292cc5d3b9a5", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in ("conversations",)])
        db = sessionmaker(bind=engine)()
        conv = ConversationRepo(db).create(title="Old item")
        other = ConversationRepo(db).create(title="Untouched")
        conv.notes = merge_notes(None, {"brand": "Levi", "nativeMarketplaceDrafts": {
            "ebay": {"state": "published", "job_id": "j1", "ebay_sku": "arabc", "ebay_offer_id": "1", "ebay_inventory_written": True,
                     "ebay_image_urls": ["https://i.ebayimg.com/a.jpg"], "listing_url": "https://www.ebay.com/itm/123456789012"},
            "etsy": {"state": "draft", "transport": "etsy_api", "etsy_image_ids": {"p1": "100"}, "etsy_photo_ids": ["p1"],
                     "etsy_photos_complete": True, "draft_url": "https://www.etsy.com/your/shops/me/listing-editor/edit/1"},
            "mercari": {"state": "unknown", "job_id": "j2", "error": "lost"}}})
        other.notes = merge_notes(None, {"brand": "Lee"})
        db.commit()
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        db.expire_all()
        # Inspect the historical migration's table, not today's ORM model:
        # newer releases can add columns in later revisions.
        from sqlalchemy import MetaData, Table, select

        table = Table("marketplace_listings", MetaData(), autoload_with=engine, resolve_fks=False)
        records = {row["marketplace"]: dict(row) for row in db.execute(
            select(table).where(table.c.conversation_id == conv.id)).mappings()}
        self.assertEqual(set(records), {"ebay", "etsy", "mercari"})
        self.assertEqual(records["ebay"]["ebay_images"], [{"photo_id": None, "checksum": None, "url": "https://i.ebayimg.com/a.jpg"}])
        self.assertTrue(records["ebay"]["ebay_inventory_written"])
        self.assertEqual(records["ebay"]["operation_id"], "j1")
        self.assertEqual(records["etsy"]["etsy_image_ids"], {"p1": "100"})
        self.assertNotIn("transport", records["etsy"])
        self.assertEqual((records["mercari"]["state"], records["mercari"]["error_code"]), ("unknown", "uncertain"))
        self.assertEqual(parse_notes(ConversationRepo(db).get(conv.id).notes), {"brand": "Levi"})
        self.assertEqual(parse_notes(ConversationRepo(db).get(other.id).notes), {"brand": "Lee"})
        db.close()
