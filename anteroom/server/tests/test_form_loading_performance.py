from __future__ import annotations

import asyncio
import contextlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401

from anteroom.database import Base
from anteroom.repositories.queries import ConversationRepo
from anteroom.services import (
    category_fields,
    listing_persistence,
    marketplace_schemas,
    mercari_form,
    native_editor,
    saved_choices,
)
from anteroom.services.ebay_client import EbayClient


class EbayFormConcurrencyAndCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'test.db'}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.enterContext(patch("anteroom.services.category_fields.SessionLocal", self.Session))
        self.enterContext(patch("anteroom.database.SessionLocal", self.Session))
        saved_choices._path().unlink(missing_ok=True)
        self.addCleanup(lambda: saved_choices._path().unlink(missing_ok=True))

    async def test_cold_ebay_form_runs_all_five_reads_together_with_one_client_connect(self):
        """Aspects, conditions, variations, listing types and policies are independent reads,
        and a cold eBay form should connect once rather than three times."""
        in_flight = peak = 0
        all_started = asyncio.Event()
        connects = 0

        async def wait_for_all(value):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            if in_flight == 5:
                all_started.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(all_started.wait(), 1.0)
            in_flight -= 1
            return value

        class StubEbayClient(EbayClient):
            async def request(self, _method, path, **_kwargs):
                if "get_item_aspects_for_category" in path:
                    return await wait_for_all({"aspects": [{
                        "localizedAspectName": "Brand",
                        "aspectConstraint": {"aspectRequired": True, "aspectUsage": "REQUIRED", "aspectMode": "FREE_TEXT",
                                             "itemToAspectCardinality": "SINGLE", "aspectMaxLength": 65},
                        "aspectValues": [],
                    }]})
                if "get_item_condition_policies" in path:
                    return await wait_for_all({"itemConditionPolicies": [{
                        "categoryId": "15687", "itemConditionRequired": True,
                        "itemConditions": [{"conditionId": "3000", "conditionDescription": "Pre-owned - Good"}],
                    }]})
                if "get_listing_structure_policies" in path:
                    return await wait_for_all({"listingStructurePolicies": [{
                        "categoryId": "15687", "variationsSupported": False,
                    }]})
                if "get_listing_type_policies" in path:
                    return await wait_for_all({"listingTypePolicies": [{
                        "categoryId": "15687",
                        "listingDurations": [{"listingType": "FIXED_PRICE", "durationValues": ["GTC"]}],
                    }]})
                raise AssertionError(f"Unexpected eBay path: {path}")

        async def connect():
            nonlocal connects
            connects += 1
            return StubEbayClient("tok", ("acct-1", "1"))

        from anteroom.services import ebay_listings

        listing = {"direct_category_ids": {"ebay": "15687"}, "condition": "Good", "price": 25}
        empty_profiles = {"payment": [], "fulfillment": [], "return": [], "locations": []}

        async def slow_profiles():
            return await wait_for_all(empty_profiles)

        with (
            patch.object(EbayClient, "connect", side_effect=connect),
            patch("anteroom.services.marketplace_accounts.account_id", new=AsyncMock(return_value="acct-1")),
            patch.object(ebay_listings, "listing_profiles", new=slow_profiles),
        ):
            form = await native_editor.ebay_form(listing)

        self.assertEqual(peak, 5, f"Expected all 5 independent eBay reads in parallel, saw peak={peak}")
        self.assertEqual(connects, 1, f"Expected 1 shared EbayClient.connect(), saw {connects}")
        self.assertIn("ebay_specifics.Brand", {row["path"] for row in form["fields"]})

    def test_warm_ebay_form_uses_cached_schemas_without_ebay_connect_or_extra_sessions(self):
        """When schemas and policies are cached, reading eBay's form needs no EbayClient.connect
        (no token refresh) and opens no nested SessionLocal checkouts."""
        conv = ConversationRepo(self.db).create(title="Vintage tee")
        listing = {
            "title": "Fruit of the Loom L Vintage Graphic T-Shirt",
            "description": "Vintage graphic tee",
            "price": 27,
            "brand": "Fruit of the Loom",
            "condition": "Good",
            "direct_category_ids": {"ebay": "15687"},
            "marketplace_categories": {"ebay": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts"},
            "marketplace_category_ids": {"ebay": "15687"},
        }
        rev = listing_persistence.save_revision(self.db, conv.id, listing, "test")
        category_fields.save_rows(marketplace_schemas.EBAY_ASPECTS, "15687", [
            {"id": "Brand", "name": "Brand", "required": True, "usage": "REQUIRED", "mode": "free_text",
             "multi": False, "max_values": None, "max_length": 65, "values": []},
        ])
        category_fields.save_rows(marketplace_schemas.EBAY_CONDITIONS, "15687", [
            {"required": True, "conditions": [{"id": "3000", "label": "Pre-owned - Good"}]},
        ])
        category_fields.save_rows(marketplace_schemas.EBAY_LISTING_TYPES, "15687", [{"FIXED_PRICE": ["GTC"]}])
        category_fields.save_rows(marketplace_schemas.EBAY_LISTING_STRUCTURE, "15687", [{"variations": False}])
        saved_choices._save("ebay-policies", "acct-1", {
            "payment": [{"id": "p1", "title": "eBay Payments"}],
            "fulfillment": [{"id": "f1", "title": "Ground"}],
            "return": [{"id": "r1", "title": "30 days"}],
            "locations": [{"id": "loc-1", "title": "Home"}],
        })

        queries: list[str] = []
        nested_sessions = 0
        original_session = self.Session

        def counting_session(*args, **kwargs):
            nonlocal nested_sessions
            nested_sessions += 1
            return original_session(*args, **kwargs)

        listen = lambda *args: queries.append(args[2])  # noqa: E731
        event.listen(self.engine, "before_cursor_execute", listen)
        try:
            with (
                patch("anteroom.services.category_fields.SessionLocal", side_effect=counting_session),
                patch("anteroom.database.SessionLocal", side_effect=counting_session),
                patch.object(EbayClient, "connect", side_effect=AssertionError("Should not connect on warm cache")),
                patch("anteroom.services.marketplace_accounts.account_id", new=AsyncMock(return_value="acct-1")),
            ):
                form = native_editor.read_form(self.db, conv.id, "ebay", rev.id)
        finally:
            event.remove(self.engine, "before_cursor_execute", listen)

        self.assertEqual(nested_sessions, 0, f"Expected 0 nested SessionLocal checkouts, saw {nested_sessions}")
        self.assertLessEqual(len(queries), 9, f"Expected <=9 SQL queries on warm eBay form, saw {len(queries)}: {queries}")
        fields = {row["path"]: row for row in form["fields"]}
        self.assertEqual(fields["ebay_specifics.paymentPolicyId"]["value"], "p1")
        self.assertEqual(fields["ebay_specifics.Condition"]["value"], "3000")

    async def test_mercari_editor_form_caches_public_shipping_quotes_until_refresh(self):
        """Identical package/category combos reuse cached public quotes; explicit refresh re-reads."""
        quote = {"id": 2552, "fee": 799, "standardFee": 899, "etaForSeller": "1 - 7 days",
                 "weightRange": {"min": 13, "max": 16}}
        listing_a = {
            "title": "Shirt A", "direct_category_ids": {"mercari": "275"},
            "weight_lb": 1, "mercari_specifics": {"package": {"fitsShoebox": "yes"}},
        }
        listing_b = {
            "title": "Shirt B", "direct_category_ids": {"mercari": "275"},
            "weight_lb": 1, "mercari_specifics": {"package": {"fitsShoebox": "yes"}},
        }
        with patch("anteroom.services.mercari_api.shipping_quotes", new=AsyncMock(return_value=[quote])) as read:
            first = await mercari_form.editor_form(listing_a)
            second = await mercari_form.editor_form(listing_b)
            self.assertEqual(read.await_count, 1, "Second listing with identical package should hit quote cache")
            refreshed = await mercari_form.editor_form(listing_a, refresh=True)
            self.assertEqual(read.await_count, 2, "Explicit refresh should bypass quote cache")

        for result in (first, second, refreshed):
            field = next(row for row in result["fields"] if row["path"] == "mercari_specifics.shippingClassId")
            self.assertEqual(field["option_values"], {"USPS Ground Advantage": "2552"})

    async def test_cancelling_first_mercari_waiter_does_not_cancel_shared_quote_fetch(self):
        """Aborting a background warm-up must not cancel another caller sharing the same in-flight quote read."""
        quote = {"id": 2552, "fee": 799, "standardFee": 899, "etaForSeller": "1 - 7 days",
                 "weightRange": {"min": 13, "max": 16}}
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_quotes(_cat, _oz, _dims):
            started.set()
            await release.wait()
            return [quote]

        listing = {
            "title": "Shirt", "direct_category_ids": {"mercari": "275"},
            "weight_lb": 1, "mercari_specifics": {"package": {"fitsShoebox": "yes"}},
        }
        with patch("anteroom.services.mercari_api.shipping_quotes", side_effect=slow_quotes):
            first = asyncio.create_task(mercari_form.editor_form(listing))
            await asyncio.wait_for(started.wait(), 1.0)
            second = asyncio.create_task(mercari_form.editor_form(listing))
            await asyncio.sleep(0)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            release.set()
            result = await asyncio.wait_for(second, 1.0)
        field = next(row for row in result["fields"] if row["path"] == "mercari_specifics.shippingClassId")
        self.assertEqual(field["option_values"], {"USPS Ground Advantage": "2552"})

    def test_save_refreshed_form_avoids_deep_copying_untouched_marketplace_contracts(self):
        """Refreshing one marketplace must not deep-copy megabytes of other marketplaces' saved contracts."""
        conv = ConversationRepo(self.db).create(title="Tee")
        untouched_poshmark = {"category_id": "men/shirts", "page_labels": True, "inputs": {}, "fields": [{"path": "price"}]}
        listing = {
            "title": "Tee",
            "price": 25,
            "direct_category_ids": {"mercari": "2008"},
            "_native_forms": {
                "poshmark": untouched_poshmark,
                "mercari": {"category_id": "old", "page_labels": True, "inputs": {"direct_category_ids.mercari": "old"},
                            "fields": [{"path": "direct_category_ids.mercari", "label": "Category"}]},
            },
        }
        rev = listing_persistence.save_revision(self.db, conv.id, listing, "native_schema")
        refreshed_form = {"marketplace": "mercari", "native": True, "known": True, "page_labels": True,
                          "category_id": "2008", "fields": [{"path": "direct_category_ids.mercari", "label": "Category"}]}
        saved_payloads: list[dict] = []
        original_save = listing_persistence.save_revision

        def spy_save(db, conv_id, payload, source, *args, **kwargs):
            saved_payloads.append(payload)
            return original_save(db, conv_id, payload, source, *args, **kwargs)

        with patch.object(listing_persistence, "save_revision", side_effect=spy_save):
            orig_get = native_editor.ListingRepo.get_revision
            loaded_poshmark: list[dict] = []

            def spy_get(repo_self, rid):
                loaded = orig_get(repo_self, rid)
                if loaded is not None and rid == rev.id:
                    loaded_poshmark.append(loaded.listing_json["_native_forms"]["poshmark"])
                return loaded

            with patch.object(native_editor.ListingRepo, "get_revision", autospec=True, side_effect=spy_get):
                result = native_editor.save_refreshed_form(self.db, conv.id, "mercari", rev.id, refreshed_form)

        self.assertNotEqual(result["revision_id"], rev.id)
        self.assertEqual(len(saved_payloads), 1)
        self.assertGreaterEqual(len(loaded_poshmark), 1)
        # Untouched Poshmark contract dict should be reused by reference rather than deep-copied.
        self.assertIs(saved_payloads[0]["_native_forms"]["poshmark"], loaded_poshmark[0])


def test_hosted_form_releases_db_connection_before_external_network_wait(migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    """Hosted form route must release its pooled Postgres connection before awaiting external APIs."""
    import httpx
    from sqlalchemy import create_engine as sa_create_engine
    from test_hosted_drafts import headers
    from test_seller_sessions import PROJECT_URL
    from anteroom.hosted import create_app
    from anteroom.services import mercari_api, schema_migrations

    if migration_engine.dialect.name != "postgresql":
        pytest.skip("Hosted data routes require PostgreSQL")
    schema_migrations.ensure_schema(migration_engine)

    # Single-connection pool proves no connection is held during the Mercari network wait:
    # while the form request waits on Mercari, a concurrent /api/status must succeed immediately.
    tight_engine = sa_create_engine(migration_engine.url, pool_size=1, max_overflow=0, pool_timeout=1.0)
    monkeypatch.setattr("anteroom.database.engine", tight_engine)
    monkeypatch.setattr("anteroom.database.SessionLocal", sessionmaker(bind=tight_engine, autoflush=False, autocommit=False))

    entered = threading.Event()
    release = threading.Event()
    quote_loops: list[asyncio.AbstractEventLoop] = []
    quote = {"id": 2552, "fee": 799, "standardFee": 899, "etaForSeller": "1 - 7 days",
             "weightRange": {"min": 13, "max": 16}}

    async def slow_quotes(_cat, _oz, _dims):
        quote_loops.append(asyncio.get_running_loop())
        entered.set()
        await asyncio.to_thread(release.wait, 3.0)
        return [quote]

    monkeypatch.setattr(mercari_api, "shipping_quotes", slow_quotes)

    async def run():
        server_loop = asyncio.get_running_loop()
        app = create_app(PROJECT_URL)
        alice = headers(signing_keys)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                created = await client.post("/api/conversations", headers=alice, json={"title": "Hosted tee"})
                assert created.status_code == 200, created.text
                conv_id = created.json()["id"]
                saved = await client.put(
                    f"/api/conversations/{conv_id}/listing",
                    headers=alice,
                    json={
                        "listing": {
                            "title": "Hosted tee",
                            "price": 25,
                            "direct_category_ids": {"mercari": "275"},
                            "mercari_specifics": {"package": {"pounds": 1, "fitsShoebox": "yes"}},
                        },
                        "expected_revision_id": "",
                    },
                )
                assert saved.status_code == 200, saved.text
                rev_id = saved.json()["revision_id"]

                form_task = asyncio.create_task(
                    client.get(f"/api/conversations/{conv_id}/native-marketplaces/mercari/form?revision_id={rev_id}",
                               headers=alice)
                )
                assert await asyncio.to_thread(entered.wait, 3.0)
                # With pool_size=1, max_overflow=0: if the form route is still holding its DB session
                # during slow_quotes, this single-connection read will time out with 503.
                other_resp = await client.get(f"/api/conversations/{conv_id}", headers=alice)
                release.set()
                form_resp = await form_task
                assert other_resp.status_code == 200, f"Pool starved while form waited on Mercari: {other_resp.text}"
                assert form_resp.status_code == 200, form_resp.text
                assert quote_loops == [server_loop], "Form outbound HTTP must run on the server loop to reuse shared_http"

    try:
        asyncio.run(run())
    finally:
        tight_engine.dispose()


def test_hosted_concurrent_multiseller_forms_coalesce_and_stay_responsive(migration_engine, jwks, signing_keys, monkeypatch):  # noqa: F811
    """Two sellers loading 24 forms + 16 listing reads concurrently on a 4-connection pool see 0 errors
    and coalesce identical public Mercari shipping quote requests."""
    import time

    import httpx
    from sqlalchemy import create_engine as sa_create_engine
    from test_hosted_drafts import BOB, headers
    from test_seller_sessions import PROJECT_URL, USER_ID
    from anteroom.hosted import create_app
    from anteroom.services import mercari_api, schema_migrations

    if migration_engine.dialect.name != "postgresql":
        pytest.skip("Hosted data routes require PostgreSQL")
    schema_migrations.ensure_schema(migration_engine)

    pooled_engine = sa_create_engine(migration_engine.url, pool_size=4, max_overflow=0, pool_timeout=5.0)
    monkeypatch.setattr("anteroom.database.engine", pooled_engine)
    monkeypatch.setattr("anteroom.database.SessionLocal", sessionmaker(bind=pooled_engine, autoflush=False, autocommit=False))

    mercari_calls = 0
    quote = {"id": 2552, "fee": 799, "standardFee": 899, "etaForSeller": "1 - 7 days",
             "weightRange": {"min": 13, "max": 16}}

    async def sim_quotes(_cat, _oz, _dims):
        nonlocal mercari_calls
        mercari_calls += 1
        await asyncio.sleep(0.15)
        return [quote]

    monkeypatch.setattr(mercari_api, "shipping_quotes", sim_quotes)

    async def run():
        app = create_app(PROJECT_URL)
        alice, bob = headers(signing_keys, USER_ID), headers(signing_keys, BOB)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                drafts = []
                for label, hdr in (("alice", alice), ("bob", bob)):
                    for i in range(4):
                        created = await client.post("/api/conversations", headers=hdr, json={"title": f"{label}-{i}"})
                        assert created.status_code == 200, created.text
                        cid = created.json()["id"]
                        saved = await client.put(
                            f"/api/conversations/{cid}/listing",
                            headers=hdr,
                            json={
                                "listing": {
                                    "title": f"{label} tee {i}",
                                    "price": 25,
                                    "direct_category_ids": {
                                        "poshmark": "men/shirts",
                                        "depop": "mens-tops-t-shirts",
                                        "mercari": "275",
                                    },
                                    "mercari_specifics": {"package": {"pounds": 1, "fitsShoebox": "yes"}},
                                },
                                "expected_revision_id": "",
                            },
                        )
                        assert saved.status_code == 200, saved.text
                        drafts.append((hdr, cid, saved.json()["revision_id"]))

                latencies: dict[str, list[float]] = {"poshmark": [], "depop": [], "mercari": [], "listing": []}

                async def load_form(hdr, cid, market, rev):
                    t0 = time.perf_counter()
                    resp = await client.get(
                        f"/api/conversations/{cid}/native-marketplaces/{market}/form?revision_id={rev}",
                        headers=hdr,
                    )
                    assert resp.status_code == 200, (market, resp.text)
                    latencies[market].append((time.perf_counter() - t0) * 1000)

                async def read_listing(hdr, cid):
                    t0 = time.perf_counter()
                    resp = await client.get(f"/api/conversations/{cid}/listing", headers=hdr)
                    assert resp.status_code == 200, resp.text
                    latencies["listing"].append((time.perf_counter() - t0) * 1000)

                tasks = []
                for hdr, cid, rev in drafts:
                    for market in ("poshmark", "depop", "mercari"):
                        tasks.append(load_form(hdr, cid, market, rev))
                    tasks.append(read_listing(hdr, cid))
                    tasks.append(read_listing(hdr, cid))
                t_all = time.perf_counter()
                await asyncio.gather(*tasks)
                wall_ms = (time.perf_counter() - t_all) * 1000

                assert mercari_calls == 1, f"Expected singleflight to coalesce 8 Mercari requests into 1, saw {mercari_calls}"
                for kind, vals in latencies.items():
                    vals.sort()
                    p50 = vals[len(vals) // 2]
                    p95 = vals[int(len(vals) * 0.95)]
                    print(f"BENCH {kind:8s}: n={len(vals)} p50={p50:.1f}ms p95={p95:.1f}ms max={vals[-1]:.1f}ms")
                print(f"BENCH total={len(tasks)} wall={wall_ms:.1f}ms mercari_calls={mercari_calls} errors=0")

    try:
        asyncio.run(run())
    finally:
        pooled_engine.dispose()


def test_schema_session_shares_failed_ebay_connect_and_retries_next_request():
    """Concurrent schema readers in one form request share a failed connect attempt instead of timing out sequentially."""
    from anteroom.services import ebay_client, marketplace_schemas

    attempts = 0
    should_fail = True

    async def connect():
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.02)
        if should_fail:
            raise TimeoutError("token refresh timed out")
        return object()

    async def run():
        nonlocal should_fail
        with patch.object(ebay_client.EbayClient, "connect", side_effect=connect):
            with marketplace_schemas.schema_session():
                results = await asyncio.gather(
                    marketplace_schemas._ebay_client(None),
                    marketplace_schemas._ebay_client(None),
                    marketplace_schemas._ebay_client(None),
                    marketplace_schemas._ebay_client(None),
                    return_exceptions=True,
                )
            assert attempts == 1, f"Expected 1 shared failed connect attempt, saw {attempts}"
            assert all(isinstance(err, TimeoutError) for err in results)

            # A subsequent form request starts a new schema_session and retries cleanly.
            should_fail = False
            with marketplace_schemas.schema_session():
                clients = await asyncio.gather(
                    marketplace_schemas._ebay_client(None),
                    marketplace_schemas._ebay_client(None),
                )
            assert attempts == 2
            assert clients[0] is clients[1]

    asyncio.run(run())


def test_mercari_shipping_quote_stale_ordinary_request_does_not_overwrite_refresh():
    """An older ordinary quote request finishing after an explicit refresh must not overwrite refreshed quotes."""
    from anteroom.services import mercari_api, mercari_form

    mercari_form.clear_quote_cache()
    stale_quote = {"id": 2552, "fee": 799, "standardFee": 899, "etaForSeller": "1 - 7 days",
                   "weightRange": {"min": 13, "max": 16}}
    fresh_quote = {"id": 2552, "fee": 949, "standardFee": 999, "etaForSeller": "1 - 7 days",
                   "weightRange": {"min": 13, "max": 16}}

    async def run():
        ordinary_started = asyncio.Event()
        release_ordinary = asyncio.Event()
        calls = 0

        async def fake_quotes(_cat, _oz, _dims):
            nonlocal calls
            calls += 1
            if calls == 1:
                ordinary_started.set()
                await release_ordinary.wait()
                return [stale_quote]
            return [fresh_quote]

        with patch.object(mercari_api, "shipping_quotes", side_effect=fake_quotes):
            ordinary_task = asyncio.create_task(
                mercari_form._cached_shipping_quotes(275, 16, (10.0, 8.0, 4.0))
            )
            await ordinary_started.wait()

            refreshed = await mercari_form._cached_shipping_quotes(275, 16, (10.0, 8.0, 4.0), refresh=True)
            assert refreshed == [fresh_quote]

            release_ordinary.set()
            ordinary_result = await ordinary_task
            assert ordinary_result == [stale_quote]

            # Subsequent ordinary read must return the refreshed quotes, not the late older fetch.
            cached = await mercari_form._cached_shipping_quotes(275, 16, (10.0, 8.0, 4.0))
            assert cached == [fresh_quote]
            assert calls == 2

    asyncio.run(run())

