import asyncio
import json
from contextlib import asynccontextmanager

import keyring
import pytest

from anteroom.services import keychain, relay_client


@pytest.mark.parametrize(("failed_seq", "expected_acks"), [(1, []), (2, [{"type": "ack", "through": 1}])])
def test_handler_failure_closes_socket_before_the_next_batch(monkeypatch, failed_seq, expected_acks):
    attempts = []
    handled = []
    acks = []
    keychain.set_relay_device({"deviceId": "device", "token": "token"})

    class Socket:
        async def __aiter__(self):
            for seqs in ([1, 2], [3]):
                yield json.dumps({"type": "events", "events": [{"seq": seq} for seq in seqs]})

        async def send(self, raw):
            acks.append(json.loads(raw))

    @asynccontextmanager
    async def connect(url, **kwargs):
        attempts.append(url)
        if len(attempts) == 2:
            raise asyncio.CancelledError
        yield Socket()

    async def handle(event):
        seq = event["seq"]
        handled.append(seq)
        if seq == failed_seq:
            raise RuntimeError("database busy")

    monkeypatch.setattr(relay_client.websockets, "connect", connect)
    monkeypatch.setattr(relay_client, "RECONNECT_BASE_S", 0)
    monkeypatch.setattr(relay_client, "_state", relay_client.StreamState())
    monkeypatch.setattr(relay_client, "_event_failures", {})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(relay_client._run(handle))

    assert acks == expected_acks
    assert handled == list(range(1, failed_seq + 1))
    assert len(attempts) == 2 and all(url.endswith("/studio/stream?after=0") for url in attempts)
    assert relay_client.stream_state()["lastError"]


def test_event_that_always_fails_is_skipped_after_the_attempt_limit(monkeypatch):
    attempts = []
    handled = []
    acks = []
    keychain.set_relay_device({"deviceId": "device", "token": "token"})
    limit = relay_client.MAX_EVENT_ATTEMPTS
    unacked = {1, 2, 3}

    class Socket:
        async def __aiter__(self):
            # The relay starts each connection at the oldest unacknowledged event.
            yield json.dumps({"type": "events", "events": [
                {"seq": seq, "source": "ebay", "eventType": "ORDER_CONFIRMATION"} for seq in sorted(unacked)
            ]})

        async def send(self, raw):
            ack = json.loads(raw)
            acks.append(ack)
            unacked.difference_update({seq for seq in list(unacked) if seq <= ack["through"]})

    @asynccontextmanager
    async def connect(url, **kwargs):
        attempts.append(url)
        if not unacked or len(attempts) > limit + 2:
            raise asyncio.CancelledError
        yield Socket()

    flaky_failed = []

    async def handle(event):
        seq = event["seq"]
        handled.append(seq)
        if seq == 1 and not flaky_failed:
            flaky_failed.append(seq)
            raise RuntimeError("database busy")
        if seq == 2:
            raise RuntimeError("eBay is disconnected")

    monkeypatch.setattr(relay_client.websockets, "connect", connect)
    monkeypatch.setattr(relay_client, "RECONNECT_BASE_S", 0)
    monkeypatch.setattr(relay_client, "_state", relay_client.StreamState())
    monkeypatch.setattr(relay_client, "_event_failures", {})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(relay_client._run(handle))

    # Seq 1 failed once, then succeeded on retry and was acknowledged, not skipped.
    assert handled.count(1) == 2
    # Seq 2 always fails: retried up to the limit, then skipped so seq 3 is recorded.
    assert handled.count(2) == limit
    assert handled.count(3) == 1
    assert acks[0] == {"type": "ack", "through": 1}
    assert acks[-1] == {"type": "ack", "through": 3}
    assert not unacked
    assert relay_client._event_failures == {}
    assert "ebay ORDER_CONFIRMATION" in relay_client.stream_state()["lastError"]


@pytest.mark.parametrize("error", [PermissionError("access denied"), TimeoutError("read timed out")])
@pytest.mark.parametrize("cached_absence", [False, True], ids=["cold-cache", "cached-absence"])
def test_unreadable_keychain_preserves_the_existing_relay_device(monkeypatch, error, cached_absence):
    device = {"deviceId": "existing-device", "token": "existing-token"}
    backend = keyring.get_keyring()
    if cached_absence:
        assert keychain.get_relay_device() is None
    # Another process may have saved the device since this process cached the store.
    backend.set_password(keychain.KEYRING_SERVICE, keychain.SECRETS_ACCOUNT, json.dumps({
        keychain.RELAY_DEVICE_ACCOUNT: json.dumps(device),
        keychain.MIGRATION_MARKER: "1",
    }))

    def unreadable(*args):
        raise error

    def unexpected_registration():
        pytest.fail("Tried to register a relay device while the Keychain was unreadable")

    monkeypatch.setattr(relay_client, "http_client", unexpected_registration)
    with monkeypatch.context() as locked:
        locked.setattr(backend, "get_password", unreadable)
        with pytest.raises(relay_client.RelayError, match="Keychain"):
            asyncio.run(relay_client.ensure_device())

    assert asyncio.run(relay_client.ensure_device()) == device
