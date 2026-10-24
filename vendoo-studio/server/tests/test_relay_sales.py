from __future__ import annotations

import asyncio
import json
import time
import unittest
from unittest import mock

import websockets
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vendoo_studio.database import Base, get_db
from vendoo_studio.main import app
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services import keychain, marketplace_accounts, relay_client, relay_sales


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def _listing(db, title, urls):
    conv = ConversationRepo(db).create(title=title)
    conv.notes = json.dumps({"vooItemId": "v-" + title, "vendooItemId": "v-" + title, "vendooListingUrls": urls})
    db.commit()
    return conv


def _ebay_event(order="12-345", lines=(("li-1", "1234567890"),), user="seller-a"):
    return {
        "seq": 1,
        "source": "ebay",
        "eventType": "ORDER_CONFIRMATION",
        "ids": {
            "orderId": [order],
            "orderLineItemId": [line for line, _ in lines],
            "listingId": [listing for _, listing in lines],
            "userId": [user],
        },
        "occurredAt": "2026-10-24T01:00:00.000Z",
    }


class ListingUrlTest(unittest.TestCase):
    def test_reads_listing_ids_from_vendoo_urls(self):
        self.assertEqual(relay_sales.listing_id_from_url("ebay", "https://www.ebay.com/itm/1234567890"), "1234567890")
        self.assertEqual(relay_sales.listing_id_from_url("ebay", "https://www.ebay.com/itm/nike-tee/1234567890?x=1"), "1234567890")
        self.assertEqual(relay_sales.listing_id_from_url("etsy", "https://www.etsy.com/listing/987654/vintage-tee"), "987654")
        self.assertEqual(relay_sales.listing_id_from_url("etsy", "https://www.etsy.com/uk/listing/987654"), "987654")
        self.assertIsNone(relay_sales.listing_id_from_url("ebay", ""))
        self.assertIsNone(relay_sales.listing_id_from_url("poshmark", "https://poshmark.com/listing/x"))


class RecordSalesTest(unittest.TestCase):
    def setUp(self):
        self.Session = sessionmaker(bind=_engine())
        with self.Session() as db:
            self.tee = _listing(db, "tee", {
                "ebay": "https://www.ebay.com/itm/1234567890",
                "poshmark": "https://poshmark.com/listing/abc",
                "etsy": "https://www.etsy.com/listing/555",
            }).id
            # Same digits inside another listing's notes must not match.
            _listing(db, "decoy", {"ebay": "https://www.ebay.com/itm/91234567890"})

    def handle(self, event, receipt=None):
        async def read_receipt(shop_id, receipt_id):
            self.assertEqual((shop_id, receipt_id), ("42", "9001"))
            return receipt

        return asyncio.run(relay_sales.handle_event(event, session_factory=self.Session, read_etsy_receipt=read_receipt))

    def test_ebay_order_matches_the_listing_once(self):
        created = self.handle(_ebay_event())
        self.assertEqual(len(created), 1)
        self.assertEqual(self.handle(_ebay_event()), [], "a repeated notification is not a second sale")
        with self.Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertEqual((sale.marketplace, sale.order_id, sale.line_id, sale.listing_id), ("ebay", "12-345", "li-1", "1234567890"))
            self.assertEqual(sale.conversation_id, self.tee)
            self.assertEqual(sale.state, "new")
            self.assertEqual(sale.sold_at.isoformat(), "2026-10-24T01:00:00")

    def test_unmatched_sales_are_still_recorded(self):
        self.handle(_ebay_event(order="99-1", lines=(("li-9", "5550001111"),)))
        with self.Session() as db:
            self.assertIsNone(db.query(MarketplaceSale).one().conversation_id)

    def test_multi_line_orders_become_one_sale_per_line(self):
        created = self.handle(_ebay_event(order="77-1", lines=(("li-a", "1234567890"), ("li-b", "5550001111"))))
        self.assertEqual(len(created), 2)

    def test_etsy_receipt_lines_come_from_the_receipt(self):
        event = {"seq": 2, "source": "etsy", "eventType": "order.paid", "ids": {"shop_id": ["42"], "receipt_id": ["9001"]}}
        receipt = {"receipt_id": 9001, "created_timestamp": 1792806707,
                   "transactions": [{"transaction_id": 31, "listing_id": 555, "quantity": 1}]}
        self.assertEqual(len(self.handle(event, receipt)), 1)
        with self.Session() as db:
            sale = db.query(MarketplaceSale).one()
            self.assertEqual((sale.marketplace, sale.order_id, sale.line_id, sale.listing_id), ("etsy", "9001", "31", "555"))
            self.assertEqual(sale.conversation_id, self.tee)

    def test_other_events_are_acknowledged_without_a_sale(self):
        self.assertEqual(self.handle({"source": "etsy", "eventType": "listing.updated", "ids": {}}), [])
        self.assertEqual(self.handle({"source": "ebay", "eventType": "MARKETPLACE_ACCOUNT_DELETION", "ids": {"userId": ["x"]}}), [])


class SalesRoutesTest(unittest.TestCase):
    def setUp(self):
        self.Session = sessionmaker(bind=_engine())
        self.db = self.Session()
        self.conv = _listing(self.db, "tee", {
            "ebay": "https://www.ebay.com/itm/1234567890",
            "poshmark": "https://poshmark.com/listing/abc",
            "mercari": "https://www.mercari.com/us/item/m1",
        })
        sale = MarketplaceSale(marketplace="ebay", order_id="12-345", line_id="li-1", listing_id="1234567890",
                               conversation_id=self.conv.id)
        self.db.add(sale)
        self.db.commit()
        self.sale_id = sale.id

        def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def test_new_sales_list_where_else_the_item_is_listed(self):
        body = self.client.get("/api/marketplace-sales").json()
        self.assertEqual(len(body["sales"]), 1)
        sale = body["sales"][0]
        self.assertEqual(sale["title"], "tee")
        self.assertEqual(sale["listedElsewhere"], ["mercari", "poshmark"])

    def test_dismiss_takes_it_off_the_new_list(self):
        self.assertEqual(self.client.post(f"/api/marketplace-sales/{self.sale_id}/dismiss").status_code, 200)
        self.assertEqual(self.client.get("/api/marketplace-sales").json()["sales"], [])
        self.assertEqual(len(self.client.get("/api/marketplace-sales?state=dismissed").json()["sales"]), 1)

    def test_delist_needs_named_marketplaces_and_confirm(self):
        self.assertEqual(self.client.post(f"/api/marketplace-sales/{self.sale_id}/delist", json={"marketplaces": []}).status_code, 422)
        with mock.patch("vendoo_studio.services.vendoo_create.run_ops") as run_ops:
            response = self.client.post(f"/api/marketplace-sales/{self.sale_id}/delist", json={"marketplaces": ["poshmark"]})
            self.assertEqual(response.status_code, 400)
            run_ops.assert_not_called()

    def test_delist_refuses_the_marketplace_it_sold_on(self):
        response = self.client.post(f"/api/marketplace-sales/{self.sale_id}/delist", json={"marketplaces": ["ebay"], "confirm": True})
        self.assertEqual(response.status_code, 400)

    def test_delist_goes_through_vendoo_and_resolves_the_sale(self):
        async def fake_run_ops(job, ops):
            self.assertEqual(ops, [{"op": "delist_item", "item_id": "v-tee", "marketplaces": ["poshmark", "mercari"]}])
            return {"results": [{"op": "delist_item", "result": {"ok": True}}]}

        with mock.patch("vendoo_studio.services.vendoo_create.run_ops", side_effect=fake_run_ops):
            response = self.client.post(
                f"/api/marketplace-sales/{self.sale_id}/delist",
                json={"marketplaces": ["Poshmark", "mercari"], "confirm": True},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        sale = self.db.get(MarketplaceSale, self.sale_id)
        self.assertEqual(sale.state, "delisted")
        self.assertEqual(sale.delisted_from["marketplaces"], ["poshmark", "mercari"])


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class SignInTest(unittest.TestCase):
    def test_sign_in_saves_the_account_and_starts_listening(self):
        replies = iter([FakeResponse(202), FakeResponse(200, {
            "status": "done", "marketplace": "ebay", "accountId": "seller-a", "username": "cris",
            "tokens": {"accessToken": "at", "refreshToken": "rt", "expiresIn": 7200, "refreshTokenExpiresIn": 100},
            "warnings": [],
        })])
        calls = []

        async def fake_request(method, path, **kwargs):
            calls.append((method, path))
            if path.endswith("/start"):
                return FakeResponse(200, {"state": "s1", "authorizeUrl": "https://auth.ebay.com/x"})
            return next(replies)

        async def scenario():
            with mock.patch.object(marketplace_accounts, "relay_request", fake_request), \
                 mock.patch.object(marketplace_accounts, "POLL_INTERVAL_S", 0), \
                 mock.patch.object(marketplace_accounts.webbrowser, "open") as opened, \
                 mock.patch.object(marketplace_accounts, "start_listening") as listen:
                out = await marketplace_accounts.start_sign_in("ebay")
                self.assertEqual(out["authorizeUrl"], "https://auth.ebay.com/x")
                opened.assert_called_once_with("https://auth.ebay.com/x")
                self.assertTrue(marketplace_accounts.status()["accounts"]["ebay"]["pending"])
                await marketplace_accounts._pending["ebay"].task
                listen.assert_called_once()

        asyncio.run(scenario())
        account = keychain.get_marketplace_accounts()["ebay"]
        self.assertEqual((account["accountId"], account["label"], account["accessToken"]), ("seller-a", "cris", "at"))
        status = marketplace_accounts.status()["accounts"]["ebay"]
        self.assertTrue(status["connected"])
        self.assertFalse(status["pending"])
        self.assertIn(("GET", "/studio/oauth/result"), calls)

    def test_a_failed_sign_in_is_reported(self):
        async def fake_request(method, path, **kwargs):
            if path.endswith("/start"):
                return FakeResponse(200, {"state": "s2", "authorizeUrl": "https://www.etsy.com/x"})
            return FakeResponse(200, {"status": "done", "marketplace": "etsy", "error": "Etsy sign-in failed. Try again."})

        async def scenario():
            with mock.patch.object(marketplace_accounts, "relay_request", fake_request), \
                 mock.patch.object(marketplace_accounts, "POLL_INTERVAL_S", 0), \
                 mock.patch.object(marketplace_accounts.webbrowser, "open"):
                await marketplace_accounts.start_sign_in("etsy")
                await marketplace_accounts._pending["etsy"].task

        asyncio.run(scenario())
        status = marketplace_accounts.status()["accounts"]["etsy"]
        self.assertFalse(status["connected"])
        self.assertEqual(status["error"], "Etsy sign-in failed. Try again.")

    def test_expired_tokens_refresh_through_the_relay(self):
        keychain.set_marketplace_account("etsy", {"accountId": "42", "accessToken": "old", "refreshToken": "r1",
                                                  "expiresAt": time.time() - 10})

        async def fake_request(method, path, **kwargs):
            self.assertEqual((method, path, kwargs["json_body"]), ("POST", "/studio/oauth/etsy/refresh", {"refreshToken": "r1"}))
            return FakeResponse(200, {"tokens": {"accessToken": "new", "refreshToken": "r2", "expiresIn": 3600}})

        with mock.patch.object(marketplace_accounts, "relay_request", fake_request):
            self.assertEqual(asyncio.run(marketplace_accounts.access_token("etsy")), "new")
        self.assertEqual(keychain.get_marketplace_accounts()["etsy"]["refreshToken"], "r2")

    def test_a_dead_refresh_token_asks_for_sign_in(self):
        keychain.set_marketplace_account("ebay", {"accessToken": "old", "refreshToken": "dead", "expiresAt": 0})

        async def fake_request(method, path, **kwargs):
            return FakeResponse(401, {"error": "sign in again"})

        with mock.patch.object(marketplace_accounts, "relay_request", fake_request), \
             self.assertRaises(marketplace_accounts.SignInRequired):
            asyncio.run(marketplace_accounts.access_token("ebay"))

    def test_account_deletion_forgets_only_that_account(self):
        keychain.set_marketplace_account("ebay", {"accountId": "seller-a"})
        marketplace_accounts.forget_deleted_ebay_account("someone-else")
        self.assertIn("ebay", keychain.get_marketplace_accounts())
        marketplace_accounts.forget_deleted_ebay_account("seller-a")
        self.assertNotIn("ebay", keychain.get_marketplace_accounts())


class StreamTest(unittest.TestCase):
    def test_events_are_recorded_then_acknowledged(self):
        """A real WebSocket round trip against a stand-in relay."""
        keychain.set_relay_device({"deviceId": "d1", "token": "device-token"})
        handled, acks, auth = [], [], []

        async def scenario():
            done = asyncio.Event()

            async def relay(socket):
                auth.append(socket.request.headers.get("Authorization"))
                await socket.send(json.dumps({"type": "events", "events": [{"seq": 1}, {"seq": 2}]}))
                async for raw in socket:
                    acks.append(json.loads(raw))
                    done.set()

            async def handle(event):
                handled.append(event["seq"])

            async with websockets.serve(relay, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                with mock.patch.object(relay_client, "RELAY_URL", f"http://127.0.0.1:{port}"):
                    task = relay_client.start_stream(handle)
                    await asyncio.wait_for(done.wait(), 5)
                    self.assertTrue(relay_client.stream_state()["connected"])
                    relay_client.stop_stream()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

        asyncio.run(scenario())
        self.assertEqual(auth, ["Bearer device-token"])
        self.assertEqual(handled, [1, 2])
        self.assertEqual(acks, [{"type": "ack", "through": 2}])

    def test_a_failing_event_is_not_acknowledged(self):
        async def handle(event):
            if event["seq"] == 2:
                raise RuntimeError("database busy")

        through = asyncio.run(relay_client._process([{"seq": 1}, {"seq": 2}, {"seq": 3}], handle))
        self.assertEqual(through, 1)


if __name__ == "__main__":
    unittest.main()
