"""Exercise the real routes and HTTP client against a local eBay stand-in."""
from __future__ import annotations

import asyncio
import json
import threading
import xml.etree.ElementTree as ET
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
from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferPolicy, EbayOfferRule
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
            self.reply(200, {"eligibleItems": [{"listingId": item_id} for item_id in state.eligible_ids] if state.eligible else []})

    def do_POST(self):
        state = self.server.state
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/ws/api.dll":
            assert self.headers["X-EBAY-API-CALL-NAME"] == "GetItem"
            assert self.headers["X-EBAY-API-IAF-TOKEN"] == "test-token"
            assert raw.startswith(b'<?xml version="1.0" encoding="utf-8"?>')
            state.reads += 1
            requested_id = ET.fromstring(raw).findtext("{urn:ebay:apis:eBLBaseComponents}ItemID")
            self.reply(200, f'''<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack><Item>
                <ItemID>{requested_id}</ItemID><Site>US</Site><ListingType>FixedPriceItem</ListingType>
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
                            read_status=200, send_status=200, paginated=False, offsets=[], reads=0, sends=[], eligible_ids=[ITEM_ID],
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


def enable_all(offers, discount=10, minimum=None):
    return offers.client.post("/api/ebay-offers/enable-all", json={
        "discount_percent": discount, "minimum_price": minimum, "confirm": True,
    })


def test_all_listings_requires_confirmation_and_valid_terms(offers):
    for body in [{"discount_percent": 10}, {"discount_percent": 10, "confirm": False},
                 *[{"discount_percent": value, "confirm": True} for value in (4, 100, True, 10.5)]]:
        assert offers.client.post("/api/ebay-offers/enable-all", json=body).status_code == 422
    for minimum in ("0", "-1", "NaN", "0.001"):
        assert enable_all(offers, minimum=minimum).status_code == 409
    assert offers.db.query(EbayOfferPolicy).count() == 0
    assert offers.remote.sends == []


def test_all_listings_covers_current_and_future_direct_listings_without_selection(offers):
    from vendoo_studio.repositories.marketplace_listings import MarketplaceListingRepo

    assert enable_all(offers).status_code == 200
    assert offers.remote.sends == []  # Enabling waits for the timer.
    assert offers.client.get("/api/ebay-offers").json()["allListings"] == {
        "enabled": True, "discountPercent": 10, "minimumPrice": "",
    }
    asyncio.run(ebay_offers.run_automation())
    assert len(offers.remote.sends) == 1
    # All scope is not subject to the manual preview's 50-listing limit.
    for index in range(55):
        offers.db.add(Conversation(id=f"cold-{index}", title=f"Cold listing {index}", status="active", notes=merge_notes(None, {
            "vendooListingUrls": {"ebay": f"https://www.ebay.com/itm/{333333333333 + index}"},
        })))
    offers.db.add(Conversation(id="future", title="ZZ future listing", status="active"))
    offers.db.commit()
    MarketplaceListingRepo(offers.db).save("future", "ebay", {
        "state": "published", "listing_url": "https://www.ebay.com/itm/222222222222",
    })
    offers.remote.eligible_ids.append("222222222222")
    asyncio.run(ebay_offers.run_automation())
    assert [row["offeredItems"][0]["listingId"] for row in offers.remote.sends] == [ITEM_ID, "222222222222"]
    assert all(row["offeredItems"][0]["price"]["value"] == "45.00" for row in offers.remote.sends)
    offers.client.post("/api/ebay-offers/pause")
    offers.db.query(EbayOfferRule).update({"last_attempt_at": None})
    offers.db.commit()
    asyncio.run(ebay_offers.run_automation())
    assert len(offers.remote.sends) == 2


def test_all_listings_waits_for_eligibility_and_works_with_empty_inventory(offers):
    offers.db.delete(offers.conv)
    offers.db.commit()
    assert enable_all(offers).status_code == 200
    asyncio.run(ebay_offers.run_automation())
    assert not offers.remote.sends
    offers.db.add(Conversation(id="new", status="active", notes=merge_notes(None, {
        "vendooListingUrls": {"ebay": f"https://www.ebay.com/itm/{ITEM_ID}"},
    })))
    offers.db.commit()
    offers.remote.eligible = False
    asyncio.run(ebay_offers.run_automation())
    assert not offers.remote.sends
    offers.remote.eligible = True
    asyncio.run(ebay_offers.run_automation())
    assert len(offers.remote.sends) == 1


def test_all_listings_obeys_floor_sales_and_account_isolation(offers):
    assert enable_all(offers, minimum="46").status_code == 200
    asyncio.run(ebay_offers.run_automation())
    assert not offers.remote.sends
    assert enable_all(offers, minimum="30").status_code == 200
    offers.db.add(MarketplaceSale(marketplace="etsy", order_id="sold", line_id="line", conversation_id="coat"))
    offers.db.commit()
    asyncio.run(ebay_offers.run_automation())
    assert not offers.remote.sends
    offers.account["ebay"]["accountId"] = "seller-2"
    assert not offers.client.get("/api/ebay-offers").json()["allListings"]["enabled"]
    asyncio.run(ebay_offers.run_automation())
    assert not offers.remote.sends


def test_all_listings_replaces_individual_rules_and_unknown_result_pauses_policy(offers):
    row = preview(offers)
    approve(offers, "enable", row["id"])
    assert enable_all(offers).status_code == 200
    offers.db.expire_all()
    assert not offers.db.query(EbayOfferRule).one().enabled
    assert approve(offers, "enable", preview(offers)["id"]).status_code == 409
    offers.remote.send_status = 500
    asyncio.run(ebay_offers.run_automation())
    assert not offers.client.get("/api/ebay-offers").json()["allListings"]["enabled"]
    assert enable_all(offers).status_code == 200
    offers.db.query(EbayOfferRule).update({"last_attempt_at": ebay_offers.now() - timedelta(days=2)})
    offers.db.commit()
    asyncio.run(ebay_offers.run_automation())
    assert len(offers.remote.sends) == 1  # Unresolved sends cannot be retried by a new policy.


def test_all_listings_recovery_pauses_interrupted_sends_without_replaying(offers):
    enable_all(offers)
    row = preview(offers, floor="0.01")
    attempt = offers.db.get(EbayOfferAttempt, row["id"])
    attempt.state = "sending"
    offers.db.commit()

    async def restart():
        ebay_offers.start()
        await asyncio.sleep(0)
        await ebay_offers.stop()

    asyncio.run(restart())
    offers.db.expire_all()
    assert attempt.state == "unknown"
    assert not offers.db.query(EbayOfferPolicy).filter_by(account_id="seller-1").one().enabled
    asyncio.run(ebay_offers.run_automation())
    assert not offers.remote.sends


@pytest.mark.parametrize("change", ["pause", "edit"])
def test_all_listings_reservation_honors_concurrent_policy_changes(offers, monkeypatch, change):
    enable_all(offers)
    approval = offers.db.query(EbayOfferPolicy).filter_by(account_id="seller-1").one().approval_id
    row = preview(offers, floor="0.01")

    async def scenario():
        with offers.sessions() as db:
            execute = db.execute
            changed = False

            def intercept(statement, *args, **kwargs):
                nonlocal changed
                if isinstance(statement, Update) and statement.table.name == "ebay_offer_policies" and not changed:
                    changed = True
                    with offers.sessions() as control:
                        if change == "pause":
                            ebay_offers.pause(control)
                        else:
                            ebay_offers.enable_all(control, 20, None)
                return execute(statement, *args, **kwargs)

            monkeypatch.setattr(db, "execute", intercept)
            with pytest.raises(ebay_offers.OfferConflict, match="changed or was paused"):
                await ebay_offers.send(db, row["id"], automatic=True, policy_approval=approval)
            assert changed

    asyncio.run(scenario())
    assert not offers.remote.sends


def test_policy_migration_upgrades_previous_release_preserving_offer_history(tmp_path):
    from alembic import command
    from vendoo_studio.services.schema_migrations import alembic_config
    from vendoo_studio.services.schema_drift import inspect_database

    path = tmp_path / "previous-offer-release.db"
    config = alembic_config(f"sqlite:///{path}")
    command.upgrade(config, "6dc4b77bc66e")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO ebay_offer_rules "
                                "(id, account_id, listing_id, discount_percent, minimum_price_cents, enabled, updated_at) "
                                "VALUES ('keep', 'seller', '123', 10, 3000, false, CURRENT_TIMESTAMP)"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT minimum_price_cents FROM ebay_offer_rules WHERE id='keep'")).scalar() == 3000
        assert connection.execute(text("SELECT count(*) FROM ebay_offer_policies")).scalar() == 0
    assert inspect_database(path).clean
    engine.dispose()
