from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vendoo_studio import sandbox
from vendoo_studio.main import app
from vendoo_studio.providers.sandbox import LISTING, REPLY, SandboxProvider
from vendoo_studio.services import listing_provider

SERVER = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path",
    [
        "/api/conversations/abc/vendoo-api/list",
        "/api/conversations/abc/vendoo-api/delist",
        "/api/vendoo-api/update",
        "/api/vendoo-api/delete",
        "/api/conversations/abc/native-marketplaces/ebay/list",
        "/api/conversations/abc/native-marketplaces/depop/update",
        "/api/conversations/abc/marketplace-listings/delist",
        "/api/listings/marketplace-actions/perform",
        "/api/ebay/actions/confirm",
        "/api/ebay-offers/send",
        "/api/promotions/p1/perform",
    ],
)
def test_refuses_every_route_that_changes_a_live_listing(path):
    assert sandbox.refusal("POST", path) == sandbox.LISTING_DETAIL


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/updates"),
        ("POST", "/api/updates/apply"),
        ("POST", "/api/desktop/chrome/connect"),
        ("POST", "/api/settings/tailscale/enable"),
        ("POST", "/api/settings/chatgpt/login"),
        ("GET", "/api/settings/claude/terminal"),
    ],
)
def test_refuses_routes_that_start_programs_reaching_outside(method, path):
    assert sandbox.refusal(method, path) == sandbox.OFFLINE_DETAIL


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/conversations"),
        ("PUT", "/api/conversations/abc/listing"),
        ("POST", "/api/conversations/abc/messages"),
        ("POST", "/api/conversations/abc/vendoo-api/save"),
        ("POST", "/api/conversations/abc/native-marketplaces/ebay/draft"),
        ("GET", "/api/conversations/abc/vendoo-api/list"),
        ("GET", "/api/status"),
    ],
)
def test_lets_local_work_through(method, path):
    assert sandbox.refusal(method, path) is None


def test_middleware_answers_403_only_in_sandbox_mode(monkeypatch):
    client = TestClient(app)
    path = "/api/conversations/abc/vendoo-api/list"

    monkeypatch.setenv(sandbox.ENV_VAR, "1")
    response = client.post(path, json={})
    assert response.status_code == 403
    assert response.json()["detail"] == sandbox.LISTING_DETAIL

    monkeypatch.delenv(sandbox.ENV_VAR)
    assert client.post(path, json={}).status_code != 403


def test_sandbox_mode_uses_the_sandbox_provider(monkeypatch):
    monkeypatch.setenv(sandbox.ENV_VAR, "1")
    assert isinstance(listing_provider.get_listing_provider(), SandboxProvider)
    assert isinstance(listing_provider.get_photo_provider(), SandboxProvider)
    assert listing_provider.provider_is_configured() is True


def test_sandbox_provider_answers_the_same_way_every_time():
    provider = SandboxProvider()

    async def reply(prompt: str) -> str:
        return "".join([chunk async for chunk in provider.chat([{"role": "user", "content": prompt}])])

    listing = asyncio.run(reply("Return the listing as JSON."))
    assert json.loads(listing.removeprefix("```json\n").removesuffix("\n```")) == LISTING
    assert asyncio.run(reply("Thanks!")) == REPLY
    assert asyncio.run(provider.analyze_photos([]))["evidence"]["brand"]["value"] == "Levi's"


def test_install_blocks_outbound_connections_but_not_loopback():
    # install() patches httpx and socket for the whole process, so it runs in its own.
    script = """
import socket, urllib.request, httpx
from vendoo_studio import sandbox
sandbox.install()
for call in (lambda: httpx.get("https://example.com", timeout=2),
             lambda: urllib.request.urlopen("http://93.184.215.14", timeout=2)):
    try:
        call()
        print("reached")
    except Exception as exc:
        print("blocked" if sandbox.BLOCKED_DETAIL in str(exc) else f"other {exc!r}")
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen()
client = socket.socket(); client.connect(server.getsockname()); print("loopback")
import keyring; print(type(keyring.get_keyring()).__name__)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": str(SERVER), "PATH": "/usr/bin:/bin"},
    )
    assert result.stdout.split() == ["blocked", "blocked", "loopback", "MemoryKeyring"], result.stderr
