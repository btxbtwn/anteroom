"""Exercise the real routes and HTTP client against a local eBay stand-in."""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.dml import Update

from vendoo_studio.database import Base, get_db, load_models
from vendoo_studio.main import app
from vendoo_studio.models.conversation import Conversation
from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferRule
from vendoo_studio.models.marketplace_sale import MarketplaceSale
from vendoo_studio.services import ebay_api, ebay_offers, marketplace_accounts
from vendoo_studio.services.vendoo_import import merge_notes

ITEM_ID = "123456789012"


class EbayStandIn(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, body, content_type="application/json"):
        raw = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        state = self.server.state
        path = urlparse(self.path)
        assert path.path == "/sell/negotiation/v1/find_eligible_items"
        assert self.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
        assert self.headers["Authorization"] == "Bearer test-token"
        state.offsets.append(int(parse_qs(path.query)["offset"][0]))
        if state.read_status != 200:
            self.reply(state.read_status, {"errors": []})
        elif state.paginated and state.offsets[-1] == 0:
            self.reply(200, {"eligibleItems": [{"listingId": "111111111111"}], "next": "ignored-untrusted-url"})
        else:
            self.reply(200, {"eligibleItems": [{"listingId": ITEM_ID}] if state.eligible else []})

    def do_POST(self):
        state = self.server.state
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/ws/api.dll":
            assert self.headers["X-EBAY-API-CALL-NAME"] == "GetItem"
            assert self.headers["X-EBAY-API-IAF-TOKEN"] == "test-token"
            assert raw.startswith(b'<?xml version="1.0" encoding="utf-8"?>')
            state.reads += 1
            self.reply(200, f'''<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack><Item>
                <ItemID>{ITEM_ID}</ItemID><Site>US</Site><ListingType>FixedPriceItem</ListingType>
                <SellingStatus><ListingStatus>{state.listing_status}</ListingStatus>
                <CurrentPrice currencyID="{state.currency}">{state.price}</CurrentPrice></SellingStatus>
                {"<Variations/>" if state.variations else ""}</Item></GetItemResponse>''', "text/xml")
        else:
            assert self.path == "/sell/negotiation/v1/send_offer_to_interested_buyers"
            assert self.headers["Authorization"] == "Bearer test-token"
            payload = json.loads(raw)
            state.sends.append(payload)
            self.reply(state.send_status, state.send_body)


@pytest.fixture
def ebay_server():
    state = SimpleNamespace(price="50.00", currency="USD", eligible=True, listing_status="Active", variations=False,
                            read_status=200, send_status=200, paginated=False, offsets=[], reads=0, sends=[],
                            send_body={"offers": [{"offerId": "offer-1"}, {"offerId": "offer-2"}]})
    server = ThreadingHTTPServer(("127.0.0.1", 0), EbayStandIn)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def offers(tmp_path, monkeypatch, ebay_server):
    engine = create_engine(f"sqlite:///{tmp_path / 'offers.db'}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    load_models()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    db = sessions()
    conv = Conversation(id="coat", title="Vintage wool coat", status="active",
                        notes=merge_notes(None, {"vendooListingUrls": {"ebay": f"https://www.ebay.com/itm/{ITEM_ID}"}}))
    db.add(conv)
    db.commit()
    accounts = {"ebay": {"accountId": "seller-1", "accessToken": "test-token"}}
    monkeypatch.setattr(ebay_offers, "get_marketplace_accounts", lambda: accounts)
    monkeypatch.setattr(ebay_offers, "SessionLocal", sessions)
    monkeypatch.setattr(ebay_api, "access_token", AsyncMock(return_value="test-token"))
    monkeypatch.setattr(ebay_api, "API_URL", ebay_server[0])
    monkeypatch.setattr(ebay_offers, "_last_error", None)
    monkeypatch.setattr(ebay_offers, "_last_check", None)

    def get_test_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    client = TestClient(app)
    fixture = SimpleNamespace(client=client, db=db, sessions=sessions, account=accounts, remote=ebay_server[1], conv=conv)
    try:
        yield fixture
    finally:
        app.dependency_overrides.clear()
        client.close()
        db.close()
        engine.dispose()


def preview(offers, floor="30.00", discount=10):
    response = offers.client.post("/api/ebay-offers/preview", json={
        "selections": [{"conversation_id": "coat", "minimum_price": floor}], "discount_percent": discount,
    })
    assert response.status_code == 200, response.text
    return response.json()["offers"][0]


def approve(offers, action, attempt_id, confirm=True):
    return offers.client.post(f"/api/ebay-offers/{action}", json={"attempt_ids": [attempt_id], "confirm": confirm})


def test_preview_then_explicit_send_uses_live_price_and_fixed_dollar_floor(offers):
    row = preview(offers)
    assert row["state"] == "preview" and row["eligible"]
    assert (row["currentPrice"], row["offerPrice"], row["minimumPrice"]) == ("50.00", "45.00", "30.00")
    assert offers.remote.sends == []
    assert not offers.db.query(EbayOfferRule).one().enabled
    response = approve(offers, "send", row["id"])
    assert response.status_code == 200
    assert response.json()["offers"][0]["state"] == "sent"
    assert response.json()["offers"][0]["recipientCount"] == 2
    assert offers.remote.sends == [{"allowCounterOffer": False, "offeredItems": [
        {"listingId": ITEM_ID, "quantity": 1, "price": {"value": "45.00", "currency": "USD"}},
    ]}]
    assert "buyer" not in json.dumps(offers.client.get("/api/ebay-offers").json()["history"][0]).replace("interested buyer", "")


def test_send_and_enable_require_explicit_confirmation(offers):
    row = preview(offers)
    for action in ("send", "enable"):
        assert approve(offers, action, row["id"], False).status_code == 422
        assert offers.client.post(f"/api/ebay-offers/{action}", json={"attempt_ids": [row["id"]]}).status_code == 422
    assert offers.remote.sends == []


@pytest.mark.parametrize("floor", ["45.01", "50", "NaN", "Infinity", "-1", "0", "0.001", ""])
def test_invalid_or_too_high_minimum_never_produces_an_approved_offer(offers, floor):
    assert preview(offers, floor)["state"] == "skipped"
    assert offers.remote.sends == []


@pytest.mark.parametrize("discount", [0, 4, 100, 10.5, True])
def test_invalid_discounts_are_rejected(offers, discount):
    response = offers.client.post("/api/ebay-offers/preview", json={
        "selections": [{"conversation_id": "coat", "minimum_price": "1"}], "discount_percent": discount,
    })
    assert response.status_code == 422


def test_rounding_does_not_increase_discount_or_cross_the_floor(offers):
    offers.remote.price = "10.01"
    row = preview(offers, "9.01")
    assert row["offerPrice"] == "9.01"
    assert preview(offers, "9.02")["state"] == "skipped"
    offers.remote.price = "0.01"
    assert preview(offers, "0.01")["state"] == "skipped"


def test_new_buyers_can_be_automated_even_when_none_are_eligible_yet(offers):
    offers.remote.eligible = False
    row = preview(offers)
    assert row["state"] == "preview" and not row["eligible"]
    assert approve(offers, "enable", row["id"]).status_code == 200
    asyncio.run(ebay_offers.run_automation())
    assert offers.remote.sends == []
    offers.remote.eligible = True
    asyncio.run(ebay_offers.run_automation())
    assert len(offers.remote.sends) == 1
    asyncio.run(ebay_offers.run_automation())
    assert len(offers.remote.sends) == 1


def test_enabling_waits_for_timer_and_pause_stops_future_checks(offers):
    row = preview(offers)
    assert approve(offers, "enable", row["id"]).status_code == 200
    assert offers.remote.sends == []
    assert offers.client.get("/api/ebay-offers").json()["listings"][0]["enabled"]
    offers.client.post("/api/ebay-offers/pause")
    asyncio.run(ebay_offers.run_automation())
    assert offers.remote.sends == []


def test_stale_price_expired_preview_changed_account_and_lost_eligibility_block_send(offers):
    row = preview(offers)
    offers.remote.price = "40.00"
    assert approve(offers, "send", row["id"]).json()["offers"][0]["state"] == "skipped"
    offers.remote.price = "50.00"
    offers.remote.eligible = False
    assert approve(offers, "send", row["id"]).json()["offers"][0]["state"] == "skipped"
    offers.remote.eligible = True
    offers.account["ebay"]["accountId"] = "seller-2"
    assert approve(offers, "enable", row["id"]).status_code == 409
    offers.account["ebay"]["accountId"] = "seller-1"
    attempt = offers.db.get(EbayOfferAttempt, row["id"])
    attempt.expires_at = ebay_offers.now() - timedelta(seconds=1)
    offers.db.commit()
    assert approve(offers, "enable", row["id"]).status_code == 409
    assert offers.remote.sends == []


def test_duplicate_submissions_and_concurrent_previews_send_only_once(offers):
    first, second = preview(offers), preview(offers)
    assert approve(offers, "send", first["id"]).json()["offers"][0]["state"] == "sent"
    assert approve(offers, "send", first["id"]).json()["offers"][0]["state"] == "skipped"
    assert approve(offers, "send", second["id"]).json()["offers"][0]["state"] == "skipped"
    assert len(offers.remote.sends) == 1


def test_two_simultaneous_sends_have_one_durable_reservation(offers):
    first, second = preview(offers), preview(offers)

    async def scenario():
        with offers.sessions() as one, offers.sessions() as two:
            return await asyncio.gather(ebay_offers.send_previews(one, [first["id"]]),
                                        ebay_offers.send_previews(two, [second["id"]]))

    results = asyncio.run(scenario())
    assert sorted(row["offers"][0]["state"] for row in results) == ["sent", "skipped"]
    assert len(offers.remote.sends) == 1


@pytest.mark.parametrize("send_status,send_body", [(500, {}), (200, {}), (200, {"offers": []})])
def test_unknown_sends_pause_and_never_retry_even_after_a_day(offers, send_status, send_body):
    row = preview(offers)
    approve(offers, "enable", row["id"])
    offers.remote.send_status, offers.remote.send_body = send_status, send_body
    asyncio.run(ebay_offers.run_automation())
    offers.db.expire_all()
    rule = offers.db.query(EbayOfferRule).one()
    assert not rule.enabled
    attempt = offers.db.query(EbayOfferAttempt).filter_by(state="unknown").one()
    rule.last_attempt_at = ebay_offers.now() - timedelta(days=2)
    offers.db.commit()
    assert preview(offers)["state"] == "skipped"
    asyncio.run(ebay_offers.run_automation())
    assert len(offers.remote.sends) == 1
    assert offers.client.post(f"/api/ebay-offers/attempts/{attempt.id}/checked", json={"confirm": True}).status_code == 200
    offers.db.expire_all()
    assert not rule.enabled
    assert preview(offers)["state"] == "preview"


def test_sold_item_and_relinked_item_are_blocked(offers):
    row = preview(offers)
    offers.db.add(MarketplaceSale(marketplace="etsy", order_id="order", line_id="line", conversation_id="coat"))
    offers.db.commit()
    assert approve(offers, "send", row["id"]).json()["offers"][0]["state"] == "skipped"
    assert offers.client.get("/api/ebay-offers").json()["listings"] == []
    assert offers.remote.sends == []


@pytest.mark.parametrize("change,value", [("currency", "EUR"), ("variations", True), ("listing_status", "Completed")])
def test_unsupported_or_ended_listings_are_skipped(offers, change, value):
    setattr(offers.remote, change, value)
    assert preview(offers)["state"] == "skipped"
    assert offers.remote.sends == []


def test_missing_permission_requests_reconnect_and_preserves_no_preview(offers):
    offers.remote.read_status = 403
    response = offers.client.post("/api/ebay-offers/preview", json={
        "selections": [{"conversation_id": "coat", "minimum_price": "30"}], "discount_percent": 10,
    })
    assert response.status_code == 403
    assert "Reconnect eBay" in response.json()["detail"]
    assert offers.db.query(EbayOfferAttempt).count() == 0


def test_eligibility_reads_every_page_without_following_external_next_urls(offers):
    offers.remote.paginated = True
    assert preview(offers)["eligible"]
    assert offers.remote.offsets == [0, 200]


def test_timer_start_recovers_interrupted_send_without_replaying(offers):
    row = preview(offers)
    attempt = offers.db.get(EbayOfferAttempt, row["id"])
    attempt.state = "sending"
    rule = offers.db.query(EbayOfferRule).one()
    rule.enabled = True
    offers.db.commit()

    async def scenario():
        ebay_offers.start()
        await asyncio.sleep(0)
        await ebay_offers.stop()

    asyncio.run(scenario())
    offers.db.expire_all()
    assert attempt.state == "unknown" and not rule.enabled
    assert offers.remote.sends == []


@pytest.mark.parametrize("change", ["pause", "disconnect", "switch-account"])
def test_changes_during_final_token_refresh_prevent_the_send(offers, monkeypatch, change):
    approved = preview(offers)
    approve(offers, "enable", approved["id"])
    row = preview(offers)
    calls = 0

    async def token(_marketplace):
        nonlocal calls
        calls += 1
        if calls == 3:
            if change == "pause":
                with offers.sessions() as session:
                    session.query(EbayOfferRule).update({"enabled": False})
                    session.commit()
            elif change == "disconnect":
                offers.account.clear()
            else:
                offers.account["ebay"]["accountId"] = "new-seller"
            await asyncio.sleep(0)
        return "test-token"

    monkeypatch.setattr(ebay_api, "access_token", token)

    async def scenario():
        with offers.sessions() as db, pytest.raises((ebay_offers.OfferConflict, marketplace_accounts.SignInRequired)):
            await ebay_offers.send(db, row["id"], automatic=True)

    asyncio.run(scenario())
    assert offers.remote.sends == []


def test_token_refresh_does_not_restore_a_disconnected_account(offers, monkeypatch):
    offers.account["ebay"].update({"refreshToken": "test-refresh", "expiresAt": 1})
    monkeypatch.setattr(marketplace_accounts, "get_marketplace_accounts", lambda: offers.account)
    saved = Mock()
    monkeypatch.setattr(marketplace_accounts, "set_marketplace_account", saved)

    async def refresh(*_args, **_kwargs):
        offers.account.clear()
        return SimpleNamespace(status_code=200, json=lambda: {"tokens": {"accessToken": "refreshed", "expiresIn": 7200}})

    monkeypatch.setattr(marketplace_accounts, "relay_request", refresh)
    with pytest.raises(marketplace_accounts.SignInRequired, match="connection changed"):
        asyncio.run(marketplace_accounts.access_token("ebay"))
    saved.assert_not_called()
    assert not offers.account


@pytest.mark.parametrize("change", ["pause", "higher-floor"])
def test_atomic_reservation_honors_concurrent_controls(offers, monkeypatch, change):
    row = preview(offers)
    approve(offers, "enable", row["id"])
    attempt = preview(offers)

    async def scenario():
        with offers.sessions() as db:
            execute = db.execute
            changed = False

            def intercept(statement, *args, **kwargs):
                nonlocal changed
                if isinstance(statement, Update) and statement.table.name == "ebay_offer_rules" and not changed:
                    changed = True
                    with offers.sessions() as control:
                        values = {"enabled": False} if change == "pause" else {"minimum_price_cents": 4700}
                        control.query(EbayOfferRule).update(values)
                        control.commit()
                return execute(statement, *args, **kwargs)

            monkeypatch.setattr(db, "execute", intercept)
            with pytest.raises(ebay_offers.OfferConflict):
                await ebay_offers.send(db, attempt["id"], automatic=True)
            assert changed

    asyncio.run(scenario())
    assert offers.remote.sends == []


@pytest.mark.parametrize("uncertain", [False, True])
def test_deleting_and_reimporting_keeps_send_protection_and_activity(offers, uncertain):
    row = preview(offers)
    if uncertain:
        offers.remote.send_status = 500
    approve(offers, "send", row["id"])
    offers.db.execute(text("DELETE FROM conversations WHERE id='coat'"))
    offers.db.commit()
    offers.db.expire_all()
    assert offers.db.query(EbayOfferRule).one().conversation_id is None
    assert offers.db.query(EbayOfferAttempt).count() == 1
    assert len(offers.client.get("/api/ebay-offers").json()["history"]) == 1
    offers.db.add(Conversation(id="reimported", title="Reimported coat", status="active",
                              notes=merge_notes(None, {"vendooListingUrls": {"ebay": f"https://www.ebay.com/itm/{ITEM_ID}"}})))
    offers.db.commit()
    response = offers.client.post("/api/ebay-offers/preview", json={
        "selections": [{"conversation_id": "reimported", "minimum_price": "30"}], "discount_percent": 10,
    })
    assert response.json()["offers"][0]["state"] == "skipped"
    assert len(offers.remote.sends) == 1


def test_new_migration_upgrades_a_previous_release_without_losing_inventory(tmp_path):
    from alembic import command
    from vendoo_studio.services.schema_migrations import alembic_config
    from vendoo_studio.services.schema_drift import inspect_database

    path = tmp_path / "previous-release.db"
    config = alembic_config(f"sqlite:///{path}")
    command.upgrade(config, "0fd9a293057b")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO conversations (id,title) VALUES ('keep','Keep this coat')"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT title FROM conversations WHERE id='keep'")).scalar() == "Keep this coat"
    assert inspect_database(path).clean
    engine.dispose()
