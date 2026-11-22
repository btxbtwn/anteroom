"""Exercise the shared expense API against mocked Plaid HTTP responses."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from test_hosted_service_storage import storage, vault_storage  # noqa: F401
from test_seller_ownership import migration_config, migration_engine  # noqa: F401

from anteroom import database
from anteroom.models.business_expense import BusinessExpense
from anteroom.models.plaid import PlaidConnection, PlaidTransaction
from anteroom.routes import business_expenses, plaid
from anteroom.services import keychain, plaid_expenses
from anteroom.services.hosted_context import HostedSettingsStore, VaultSecretStore
from anteroom.services.plaid_client import PlaidClient
from anteroom.services.service_context import ServiceContext, use

EXPENSES = "/api/business-expenses"
ROOT = f"{EXPENSES}/plaid"
DATE = "2026-01-10"
RANGE = {"start": DATE, "end": "2026-02-01"}
ALICE = "5fb39710-bcfb-46b3-a94a-a010a649bdc1"
BOB = "847a2283-34ae-48cd-a931-2f123e5a5173"


def transaction(transaction_id, **fields):
    return {
        "transaction_id": transaction_id, "date": DATE, "amount": 14.25, "iso_currency_code": "USD",
        "name": "Pirate Ship", "pending": False, **fields,
    }


def page(cursor, *, added=(), modified=(), removed=(), more=False):
    return {
        "added": list(added), "modified": list(modified),
        "removed": [{"transaction_id": key} for key in removed],
        "next_cursor": cursor, "has_more": more,
    }


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("PLAID_CLIENT_ID", "test-client")
    monkeypatch.setenv("PLAID_SECRET", "test-secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    pages = {}
    calls = []
    failures = {}
    real_client = httpx.AsyncClient

    def respond(request):
        assert request.url.host == "sandbox.plaid.com"
        body = json.loads(request.content)
        assert body.pop("client_id") == "test-client"
        assert body.pop("secret") == "test-secret"
        path = request.url.path
        calls.append((path, body))
        if path in failures:
            return httpx.Response(400, json={"error_code": failures[path], "error_message": "private upstream data"})
        match path:
            case "/link/token/create":
                assert body["user"]["client_user_id"]
                assert body["products"] == ["transactions"]
                assert body["transactions"] == {"days_requested": 730}
                return httpx.Response(200, json={"link_token": "fixture-link"})
            case "/item/public_token/exchange":
                return httpx.Response(200, json={"access_token": "test-access-" + body["public_token"]})
            case "/accounts/get":
                return httpx.Response(200, json={
                    "item": {"institution_id": "ins-" + body["access_token"].removeprefix("test-access-")},
                    "accounts": [{"name": "Checking", "mask": "7777", "type": "depository", "balances": {"current": 42}}],
                })
            case "/transactions/sync":
                response = pages.get(body.get("cursor"), page("first"))
                if isinstance(response, list):
                    response = response.pop(0)
                if isinstance(response, str):
                    return httpx.Response(400, json={"error_code": response})
                return httpx.Response(200, json=response)
            case "/item/remove":
                return httpx.Response(200, json={"removed": True})
        raise AssertionError(path)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    return pages, calls, failures


@pytest.fixture
def local(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'plaid.db'}")
    config = migration_config(engine)
    command.upgrade(config, "049b76a1ce20")
    assert "plaid_connections" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    monkeypatch.setattr(database, "_local_sessions", lambda: Session(engine))
    app = FastAPI()
    app.include_router(business_expenses.router)
    app.include_router(plaid.router)
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def connect(client, name="Bank", token="bank"):
    response = client.post(f"{ROOT}/exchange", json={"institution_name": name, "public_token": token})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert "access_token" not in payload
    return payload


def list_expenses(client):
    return {row["description"]: row for row in client.get(EXPENSES, params=RANGE).json()}


def test_unconfigured_is_safe(local, monkeypatch):
    client, _ = local
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)
    assert client.get(f"{ROOT}/status").json() == {"configured": False, "max_connections": 2, "connections": []}
    response = client.post(f"{ROOT}/link-token")
    assert response.status_code == 503
    assert "PLAID_CLIENT_ID" in response.json()["detail"]


def test_initial_and_incremental_sync_preserve_seller_intent(local, provider):
    client, engine = local
    pages, calls, _ = provider
    pages[None] = page("paging", more=True, added=[
        transaction("a", name="Pirate Ship"), transaction("b", name="Staples"),
        transaction("c", name="Seller edited"), transaction("d", name="Seller excluded"),
        transaction("pending", pending=True), transaction("income", amount=-100),
        transaction("payment", personal_finance_category={"primary": "LOAN_PAYMENTS"}),
    ])
    pages["paging"] = page("initial", added=[transaction("software", name="Vendoo")])
    assert client.post(f"{ROOT}/link-token").json() == {"link_token": "fixture-link", "redirect_uri": None}
    connection = connect(client)
    connection_id = connection["id"]
    sync = f"{ROOT}/connections/{connection_id}/sync"
    assert connection["status"] == "current"
    assert connection["last_synced_at"].endswith("Z")
    assert connection["accounts"] == [{"name": "Checking", "mask": "7777", "type": "depository", "subtype": None}]
    assert [body.get("cursor") for path, body in calls if path == "/transactions/sync"] == [None, "paging"]
    expenses = list_expenses(client)
    assert set(expenses) == {"Pirate Ship", "Staples", "Seller edited", "Seller excluded", "Vendoo"}
    assert all(row["import_source"] == "plaid" for row in expenses.values())
    assert expenses["Pirate Ship"]["category"] == "shipping_labels"
    assert expenses["Staples"]["category"] == "shipping_supplies"
    assert expenses["Vendoo"]["category"] == "software_subscriptions"

    edited = expenses["Seller edited"]
    edited = client.put(f"{EXPENSES}/{edited['id']}", json={
        **edited, "amount_cents": 500, "notes": "Seller note", "expected_revision": edited["revision"],
    }).json()
    excluded = expenses["Seller excluded"]
    assert client.delete(f"{EXPENSES}/{excluded['id']}", params={"expected_revision": excluded["revision"]}).status_code == 204
    pages["initial"] = page("incremental", added=[transaction("posted", name="Posted", pending_transaction_id="pending")], modified=[
        transaction("a", name="Pirate Ship updated", amount=21.74, date="2026-01-12", merchant_name="Pirate Ship"),
        transaction("d", name="Never recreate"),
    ], removed=["b", "c", "pending"])
    assert client.post(sync).json()["status"] == "current"
    expenses = list_expenses(client)
    assert set(expenses) == {"Pirate Ship updated", "Seller edited", "Posted", "Vendoo"}
    assert expenses["Seller edited"] == edited
    assert expenses["Pirate Ship updated"]["amount_cents"] == 2174
    assert expenses["Pirate Ship updated"]["vendor"] == "Pirate Ship"
    assert expenses["Pirate Ship updated"]["incurred_date"] == "2026-01-12"
    pages["incremental"] = page("repeat", added=[
        transaction("b", name="Restored by Plaid"), transaction("d", name="Still excluded"),
        transaction("posted", name="Posted"),
    ], modified=[transaction("c", name="Preserve seller edit")])
    assert client.post(sync).status_code == 200
    assert set(list_expenses(client)) == {"Pirate Ship updated", "Seller edited", "Posted", "Vendoo", "Restored by Plaid"}
    with Session(engine) as db:
        row = db.get(PlaidConnection, connection_id)
        assert row.cursor == "repeat"
        assert db.scalar(select(func.count()).select_from(PlaidTransaction)) == 6
        assert all(len(key) == 64 for key in db.scalars(select(BusinessExpense.import_key)))


def test_restarts_an_unstable_page_and_never_commits_partial_import(local, provider):
    client, _ = local
    pages, calls, _ = provider
    pages[None] = [page("unstable", added=[transaction("ghost")], more=True),
                   page("restart", added=[transaction("valid")])]
    pages["unstable"] = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
    row = connect(client)
    assert [body.get("cursor") for path, body in calls if path == "/transactions/sync"] == [None, "unstable", None]
    expenses = list_expenses(client)
    assert len(expenses) == 1
    pages["restart"] = page("error", more=True, added=[transaction("partial", name="Partial")])
    pages["error"] = "ITEM_LOGIN_REQUIRED"
    result = client.post(f"{ROOT}/connections/{row['id']}/sync").json()
    assert result["status"] == "error"
    assert "reconnect" in result["last_error"]
    assert result["last_synced_at"] == row["last_synced_at"]
    assert list_expenses(client) == expenses


def test_initial_history_can_arrive_after_first_cursor(local, provider):
    client, engine = local
    pages, _, _ = provider
    pages[None] = {**page("initial"), "transactions_update_status": "INITIAL_UPDATE_COMPLETE"}
    result = connect(client)
    assert result["status"] == "waiting" and result["has_cursor"]
    with Session(engine) as db:
        row = db.get(PlaidConnection, result["id"])
        row.last_attempted_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=16)
        db.commit()
        assert plaid_expenses._due(db, "sandbox") == [row.id]
    historic = transaction("historic", date="2024-12-12")
    pages["initial"] = {**page("complete", added=[historic]), "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"}
    assert client.post(f"{ROOT}/connections/{result['id']}/sync").json()["status"] == "current"
    expenses = client.get(EXPENSES, params={"start": "2024-11-22", "end": DATE}).json()
    assert expenses[0]["incurred_date"] == historic["date"]


def test_capacity_and_best_effort_disconnect_preserve_expenses(local, provider):
    client, engine = local
    pages, calls, failures = provider
    pages[None] = page("first", added=[transaction("expense")])
    first = connect(client)
    assert client.post(f"{ROOT}/exchange", json={"public_token": "bank"}).status_code == 409
    connect(client, "Second", "second")
    assert client.post(f"{ROOT}/link-token").status_code == 409
    assert client.post(f"{ROOT}/exchange", json={"public_token": "third"}).status_code == 409
    path = f"{ROOT}/connections/{first['id']}"
    assert keychain.read_slot(f"plaid:{first['id']}") == "test-access-bank"
    failures["/item/remove"] = "INTERNAL_SERVER_ERROR"
    assert client.delete(path).status_code == 204
    assert ("/item/remove", {"access_token": "test-access-bank"}) in calls
    assert keychain.read_slot(f"plaid:{first['id']}") is None
    assert client.post(f"{path}/sync").status_code == 404
    assert "Pirate Ship" in list_expenses(client)
    with Session(engine) as db:
        assert db.get(PlaidConnection, first["id"]) is None


def test_three_simultaneous_exchanges_never_exceed_capacity(local, provider):
    client, _ = local
    with ThreadPoolExecutor(max_workers=3) as workers:
        attempts = [workers.submit(client.post, f"{ROOT}/exchange", json={"public_token": str(i)}) for i in range(3)]
    assert sorted(attempt.result().status_code for attempt in attempts) == [201, 201, 409]
    assert len(client.get(f"{ROOT}/status").json()["connections"]) == 2


def test_scheduler_selects_only_due_connections(local, provider):
    client, engine = local
    connect(client)
    with Session(engine) as db:
        assert plaid_expenses._due(db, "sandbox") == []
        row = db.scalars(select(PlaidConnection)).one()
        row.last_attempted_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        db.commit()
        assert plaid_expenses._due(db, "sandbox") == [row.id]
        assert plaid_expenses._due(db, "production") == []


def test_hosted_never_checks_out_a_seller_slot_during_plaid_http(vault_storage, provider, monkeypatch):  # noqa: F811
    engine, sessions = vault_storage
    client = PlaidClient("sandbox", "test-client", "test-secret")
    original = PlaidClient.post
    from anteroom.services.hosted_database import active_seller_session
    from anteroom.services.account_import import imported_tables

    async def check(self, path, **body):
        assert active_seller_session(ALICE) is None
        assert engine.pool.checkedout() == 0 if hasattr(engine.pool, "checkedout") else True
        return await original(self, path, **body)

    monkeypatch.setattr(PlaidClient, "post", check)
    alice_sessions = sessions(ALICE)
    alice_secrets = VaultSecretStore(alice_sessions, ALICE)
    with use(ServiceContext(ALICE, alice_sessions, HostedSettingsStore(alice_sessions, ALICE), alice_secrets, None)):
        result = asyncio.run(plaid_expenses.exchange(client, "bank", "Bank"))
        assert alice_secrets.get(f"plaid:{result.id}") == "test-access-bank"
        with engine.connect() as connection:
            assert b"test-access-bank" not in bytes(connection.scalar(text("SELECT secret FROM vault.secrets")))
        with sessions(BOB)() as bob:
            assert not plaid_expenses.status(bob, client).connections
            assert not bob.execute(text("UPDATE plaid_connections SET institution_name = 'changed' "
                                        "WHERE id = :id RETURNING id"), {"id": result.id}).all()
            with pytest.raises(DBAPIError):
                bob.execute(text("INSERT INTO plaid_transactions (id, user_id, import_key) VALUES ('forged', :alice, 'key')"),
                            {"alice": ALICE})
        asyncio.run(plaid_expenses.disconnect(client, result.id))
        assert alice_secrets.get(f"plaid:{result.id}") is None
    assert {"plaid_connections", "plaid_transactions"}.isdisjoint(table.name for table in imported_tables())
