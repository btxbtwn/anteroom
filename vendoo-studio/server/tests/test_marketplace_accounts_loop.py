"""Saved marketplace accounts are read and written off the request loop.

On the web they live in the seller's Vault, a database read. Waiting for a
pooled connection on the request loop stalls every request until the pool
times out, so these reads run in worker threads.
"""
import asyncio
import threading
import time

import httpx
import pytest
from fastapi import HTTPException

from vendoo_studio.routes import marketplace_accounts as accounts_route
from vendoo_studio.services import marketplace_accounts, relay_client
from vendoo_studio.services.hosted_context import CredentialStorageUnavailable


def test_access_token_reads_and_refreshes_the_saved_account_in_a_worker_thread(monkeypatch):
    threads = []
    saved = {"etsy": {"accessToken": "old", "refreshToken": "r", "expiresAt": time.time() - 1, "accountId": "a", "connectedAt": 1}}

    def read():
        threads.append(threading.get_ident())
        return {name: dict(account) for name, account in saved.items()}

    def update(mutate):
        threads.append(threading.get_ident())
        mutate(saved)

    async def relay(method, path, **kwargs):
        return httpx.Response(200, json={"tokens": {"accessToken": "new", "expiresIn": 3600}})

    monkeypatch.setattr(marketplace_accounts, "get_marketplace_accounts", read)
    monkeypatch.setattr(marketplace_accounts, "update_marketplace_accounts", update)
    monkeypatch.setattr(marketplace_accounts, "relay_request", relay)

    async def run():
        return await marketplace_accounts.access_token("etsy"), threading.get_ident()

    token, loop_thread = asyncio.run(run())
    assert token == "new" and saved["etsy"]["accessToken"] == "new"
    assert threads and loop_thread not in threads


def test_the_relay_device_is_read_in_a_worker_thread(monkeypatch):
    threads = []

    def device():
        threads.append(threading.get_ident())
        return {"deviceId": "d", "token": "t"}

    monkeypatch.setattr(relay_client, "get_relay_device", device)

    async def run():
        return await relay_client.ensure_device(), threading.get_ident()

    found, loop_thread = asyncio.run(run())
    assert found["deviceId"] == "d" and threads and loop_thread not in threads


def test_sign_in_blames_the_relay_only_when_it_cannot_be_reached(monkeypatch):
    async def offline(_marketplace, **_kwargs):
        raise httpx.ConnectError("offline")

    async def storage_busy(_marketplace, **_kwargs):
        raise CredentialStorageUnavailable()

    monkeypatch.setattr(marketplace_accounts, "start_sign_in", offline)
    with pytest.raises(HTTPException) as error:
        asyncio.run(accounts_route.connect("etsy"))
    assert error.value.status_code == 503 and "relay" in error.value.detail

    # A busy credential store is reported as itself (the hosted app answers 503), not as the relay.
    monkeypatch.setattr(marketplace_accounts, "start_sign_in", storage_busy)
    with pytest.raises(CredentialStorageUnavailable):
        asyncio.run(accounts_route.connect("etsy"))


def _saved_accounts(threads):
    saved = {name: {"accessToken": "t", "expiresAt": time.time() + 3600, "accountId": "123", "connectedAt": 1}
             for name in ("ebay", "etsy")}

    def read():
        threads.append(threading.get_ident())
        return {name: dict(account) for name, account in saved.items()}
    return read


def test_ebay_client_reads_the_connected_account_in_a_worker_thread(monkeypatch):
    """The hosted eBay workspace answered 503 when this read waited for a pooled
    connection on the request loop and froze every other request meanwhile."""
    from vendoo_studio.services import ebay_client

    threads = []
    read = _saved_accounts(threads)
    monkeypatch.setattr(ebay_client, "get_marketplace_accounts", read)
    monkeypatch.setattr(marketplace_accounts, "get_marketplace_accounts", read)

    async def run():
        return await ebay_client.EbayClient.connect(), threading.get_ident()

    client, loop_thread = asyncio.run(run())
    assert client.seller == ("123", "1")
    assert threads and loop_thread not in threads


def test_etsy_client_reads_the_connected_account_in_a_worker_thread(monkeypatch):
    from vendoo_studio.services import etsy_workspace, keychain

    threads = []
    monkeypatch.setattr(keychain, "get_marketplace_accounts", _saved_accounts(threads))

    async def run():
        return await etsy_workspace.EtsyClient.connect(), threading.get_ident()

    client, loop_thread = asyncio.run(run())
    assert client.shop_id == "123"
    assert threads and loop_thread not in threads
