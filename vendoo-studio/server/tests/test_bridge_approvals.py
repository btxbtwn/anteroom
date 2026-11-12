"""Signed approvals for relayed writes: what the server signs, and that the Bridge's own check accepts it."""
import asyncio
import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from test_hosted_browser_relay import direct_mode  # noqa: F401
from test_seller_sessions import USER_ID
from vendoo_studio.services import bridge_approvals, browser_bridge, hosted_browser_relay
from vendoo_studio.services.bridge_approvals import SIGNED_APPROVALS, SIGNED_COMMANDS_CAPABILITY
from vendoo_studio.services.extension_connection import MARKETPLACE_ACTIONS, current_extension
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use

EXTENSION = Path(__file__).resolve().parents[3] / 'vendoo-extension'
UPDATE = {'type': 'marketplace.execute', 'job_id': 'job-1', 'message_id': 'a' * 32,
          'payload': {'marketplace': 'poshmark', 'action': 'update', 'listing_id': 'b' * 24, 'confirm': True,
                      'price': 25.0, 'request_id': 'a' * 32}}


@pytest.fixture
def key(monkeypatch):
    """A P-256 key made for this test, never a committed one."""
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    monkeypatch.setenv(bridge_approvals.KEY_ENV, pem)
    spki = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return SimpleNamespace(private=private, public=private.public_key(), spki=base64.b64encode(spki).decode())


def _claims(token, public):
    return jwt.decode(token, public, algorithms=['ES256'], audience='anteroom-bridge', issuer='anteroom')


def test_an_approval_binds_the_seller_command_listing_and_its_hash(key):
    token = bridge_approvals.approval(USER_ID.upper(), UPDATE)
    assert token is not None
    claims = _claims(token, key.public)
    assert claims['sub'] == USER_ID.lower()
    assert (claims['cmd'], claims['mkt'], claims['act'], claims['lid'], claims['job'], claims['mid']) == (
        'marketplace.execute', 'poshmark', 'update', 'b' * 24, 'job-1', 'a' * 32)
    assert claims['exp'] - claims['iat'] == bridge_approvals.APPROVAL_TTL_S == 300
    assert claims['sha'] == bridge_approvals.command_hash(UPDATE)
    other = bridge_approvals.approval(USER_ID, UPDATE)
    assert other is not None and _claims(other, key.public)['jti'] != claims['jti'], 'each approval is single-use'
    edited = {**UPDATE, 'payload': {**UPDATE['payload'], 'price': 1}}
    assert bridge_approvals.command_hash(edited) != claims['sha']
    with_approval = {**UPDATE, 'payload': {**UPDATE['payload'], 'approval': token}}
    assert bridge_approvals.command_hash(with_approval) == claims['sha']


def test_without_a_key_nothing_is_signed(monkeypatch):
    monkeypatch.delenv(bridge_approvals.KEY_ENV, raising=False)
    assert bridge_approvals.approval(USER_ID, UPDATE) is None
    assert bridge_approvals.signed(USER_ID, UPDATE, {SIGNED_APPROVALS}) == UPDATE


def test_a_key_that_is_not_p256_is_refused(monkeypatch):
    rsa_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    monkeypatch.setenv(bridge_approvals.KEY_ENV, rsa_pem)
    with pytest.raises(ValueError, match='P-256'):
        bridge_approvals.signing_key()


def test_only_writes_for_a_bridge_that_checks_them_are_signed(key):
    signed = bridge_approvals.signed(USER_ID, UPDATE, {MARKETPLACE_ACTIONS, SIGNED_APPROVALS})
    assert _claims(signed['payload']['approval'], key.public)['cmd'] == 'marketplace.execute'
    # An older Bridge rejects the extra field on some commands, and could not check it anyway.
    assert bridge_approvals.signed(USER_ID, UPDATE, {MARKETPLACE_ACTIONS}) == UPDATE
    for kind in ('job.cancel', 'marketplace.sessions', 'marketplace.promotion_check', 'marketplace.promotion_result'):
        read = {'type': kind, 'job_id': None, 'message_id': 'a' * 32, 'payload': {'request_id': 'a' * 32}}
        assert bridge_approvals.signed(USER_ID, read, {SIGNED_APPROVALS}) == read
    for kind in ('job.vendoo_api', 'marketplace.execute', 'marketplace.result', 'marketplace.delete', 'marketplace.promote'):
        write = {'type': kind, 'job_id': 'job-1', 'message_id': 'a' * 32, 'payload': {'request_id': 'a' * 32}}
        assert 'approval' in bridge_approvals.signed(USER_ID, write, {SIGNED_APPROVALS})['payload'], kind


def test_canonical_json_matches_what_the_bridge_rebuilds():
    # The same value and string as tests/cloud-approvals.test.js in the extension.
    value = {'b': [1, 2.5, None, True], 'a': 'é"\n', 'c': {'z': 0.1, 'y': 1e21, 'x': 1e-7, 'w': -0.0}}
    assert bridge_approvals.canonical(value) == '{"a":"é\\"\\n","b":[1,2.5,null,true],"c":{"w":0,"x":1e-7,"y":1e+21,"z":0.1}}'
    cases = {25.0: '25', 0.000001: '0.000001', 1.5e21: '1.5e+21', 123.456: '123.456', -3.5: '-3.5', 1e20: '100000000000000000000',
             2.5e-7: '2.5e-7', 1.0e16: '10000000000000000', 0.1 + 0.2: '0.30000000000000004'}
    for number, text in cases.items():
        assert bridge_approvals.canonical(number) == text, number


def test_a_bridge_that_checks_every_command_gets_an_approval_on_reads_and_cancel_too(key):
    capabilities = {MARKETPLACE_ACTIONS, SIGNED_APPROVALS, SIGNED_COMMANDS_CAPABILITY}
    for kind in ('job.cancel', 'marketplace.sessions', 'marketplace.connect', 'marketplace.sales_history',
                 'marketplace.import.page', 'marketplace.import.listing', 'marketplace.like_events',
                 'marketplace.promotion_check', 'marketplace.promotion_result'):
        read = {'type': kind, 'job_id': None, 'message_id': 'a' * 32, 'payload': {'request_id': 'a' * 32}}
        claims = _claims(bridge_approvals.signed(USER_ID, read, capabilities)['payload']['approval'], key.public)
        assert claims['cmd'] == kind and claims['pho'] == [], kind
    photos = [{'id': 'p1', 'sha256': 'ab' * 32}]
    claims = _claims(bridge_approvals.signed(USER_ID, UPDATE, capabilities, photos)['payload']['approval'], key.public)
    assert claims['pho'] == photos
    # A Bridge from before does not check photos, so its approvals say nothing about them.
    assert 'pho' not in _claims(bridge_approvals.signed(USER_ID, UPDATE, {SIGNED_APPROVALS}, photos)['payload']['approval'], key.public)


def test_photo_hashes_come_from_the_jobs_approved_copies():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from vendoo_studio.database import Base
    from vendoo_studio.models.conversation import Conversation
    from vendoo_studio.models.job import Job
    from vendoo_studio.models.listing import Listing, ListingRevision  # noqa: F401

    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        conversation = Conversation(title='Jeans')
        db.add(conversation)
        db.commit()
        approved = Job(conversation_id=conversation.id, approved_revision_id='r', status='dispatched', listing_snapshot={
            '_approved_photos': [{'id': 'p1', 'stored_filename': 'a.jpg', 'mime_type': 'image/jpeg', 'checksum': 'AB' * 32},
                                 {'id': 'p2', 'stored_filename': 'b.jpg', 'mime_type': 'image/jpeg'}]})
        unpinned = Job(conversation_id=conversation.id, approved_revision_id='r', status='dispatched', listing_snapshot={})
        db.add_all([approved, unpinned])
        db.commit()
        # A row without a checksum is left out, so the Bridge refuses that photo rather than trusting it.
        assert bridge_approvals.photo_hashes(db, approved.id) == [{'id': 'p1', 'sha256': 'ab' * 32}]
        assert bridge_approvals.photo_hashes(db, unpinned.id) == []
        assert bridge_approvals.photo_hashes(db, 'missing') == []


def _bridge_verifies(spki: str, command: dict, account: str, photos: list | None = None) -> object:
    """What the extension's own approval check answers for ``command``: null when it runs."""
    script = f"""
      const {{ createApprovalVerifier }} = require({json.dumps(str(EXTENSION / 'background' / 'cloud-approvals.js'))});
      let data = {{}};
      const api = {{ storage: {{ local: {{ get: async () => data, set: async value => {{ data = {{ ...data, ...value }}; }} }} }} }};
      const verifier = createApprovalVerifier(api, {{ publicKey: {json.dumps(spki)} }});
      const command = JSON.parse({json.dumps(json.dumps(command))});
      const photos = JSON.parse({json.dumps(json.dumps(photos or []))});
      verifier.verify(command, {json.dumps(account)}, photos).then(result => console.log(JSON.stringify(result?.approval ?? null)));
    """
    return json.loads(subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True).stdout)


def test_the_bridge_accepts_what_the_server_signs_and_nothing_else(key):
    listing = {**UPDATE, 'payload': {**UPDATE['payload'], 'title': 'Levi’s 501 — “vintage”\n', 'weight_oz': 12.5,
                                     'photos': [{'id': 'p1', 'mime_type': 'image/jpeg'}], 'tiny': 1e-7}}
    signed = bridge_approvals.signed(USER_ID, listing, {SIGNED_APPROVALS})
    assert _bridge_verifies(key.spki, signed, USER_ID) is None
    edited = {**signed, 'payload': {**signed['payload'], 'price': 1}}
    assert _bridge_verifies(key.spki, edited, USER_ID) == 'mismatch'
    assert _bridge_verifies(key.spki, signed, '22222222-2222-4222-8222-222222222222') == 'mismatch'
    other = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert _bridge_verifies(base64.b64encode(other).decode(), signed, USER_ID) == 'invalid'
    assert _bridge_verifies('', signed, USER_ID) == 'no_key'


def test_the_relay_signs_a_write_as_the_page_takes_it(direct_mode, key):  # noqa: F811
    hosted_browser_relay._relays.clear()
    capabilities = {MARKETPLACE_ACTIONS, SIGNED_APPROVALS}

    async def scenario():
        with use(for_seller(USER_ID)):
            relay = current_extension()
            await relay.next_message(capabilities, 0.01)
            request = asyncio.create_task(browser_bridge.request(SimpleNamespace(id='job-1'), 'marketplace.execute',
                                                                 {'marketplace': 'poshmark', 'action': 'list', 'confirm': True}, timeout=5))
            execute = await relay.next_message(capabilities, 1)
            relay.resolve_wait(execute['message_id'], {'ok': True})
            await request
        return execute

    execute = asyncio.run(scenario())
    claims = _claims(execute['payload']['approval'], key.public)
    assert claims['sub'] == USER_ID and claims['mid'] == execute['message_id'] and claims['act'] == 'list'
    assert _bridge_verifies(key.spki, execute, USER_ID) is None


def test_the_relay_binds_a_lists_photos_and_the_bridge_refuses_swapped_bytes(direct_mode, key, monkeypatch):  # noqa: F811
    import hashlib

    hosted_browser_relay._relays.clear()
    capabilities = {MARKETPLACE_ACTIONS, SIGNED_APPROVALS, SIGNED_COMMANDS_CAPABILITY}
    front = b'\xff\xd8approved front photo'
    asked = []

    async def hashes(job_id):
        asked.append(job_id)
        return [{'id': 'p1', 'sha256': hashlib.sha256(front).hexdigest()}]
    monkeypatch.setattr(hosted_browser_relay, 'approved_photo_hashes', hashes)

    async def scenario():
        with use(for_seller(USER_ID)):
            relay = current_extension()
            await relay.next_message(capabilities, 0.01)
            request = asyncio.create_task(browser_bridge.request(SimpleNamespace(id='job-1'), 'marketplace.execute', {
                'marketplace': 'poshmark', 'action': 'list', 'confirm': True,
                'photos': [{'id': 'p1', 'mime_type': 'image/jpeg'}]}, timeout=5))
            execute = await relay.next_message(capabilities, 1)
            relay.resolve_wait(execute['message_id'], {'ok': True})
            await request
        return execute

    execute = asyncio.run(scenario())
    assert asked == ['job-1']
    photo = lambda data: {'id': 'p1', 'mime_type': 'image/jpeg', 'base64': base64.b64encode(data).decode()}  # noqa: E731
    assert _bridge_verifies(key.spki, execute, USER_ID, [photo(front)]) is None
    assert _bridge_verifies(key.spki, execute, USER_ID, [photo(b'\xff\xd8another photo')]) == 'photos'
    assert _bridge_verifies(key.spki, execute, USER_ID, [{**photo(front), 'id': 'p2'}]) == 'photos'


def test_values_json_cannot_hold_are_signed_as_the_page_receives_them(key):
    from datetime import UTC, datetime
    from decimal import Decimal

    from fastapi.encoders import jsonable_encoder

    write = {**UPDATE, 'payload': {**UPDATE['payload'], 'price': Decimal('19.99'), 'at': datetime(2026, 11, 6, tzinfo=UTC)}}
    signed = bridge_approvals.signed(USER_ID, write, {SIGNED_APPROVALS})
    # The route returns this dict; FastAPI encodes it the same way before the page reads it.
    received = json.loads(json.dumps(jsonable_encoder(signed)))
    assert _bridge_verifies(key.spki, received, USER_ID) is None


def test_the_relay_takes_every_capability_a_current_bridge_declares():
    import re

    from vendoo_studio.routes.hosted_browser_relay import Heartbeat

    source = (EXTENSION / 'background' / 'cloud-connection.js').read_text()
    declared = re.findall(r"'([a-z_.]+)'", re.search(r'const RELAY_CAPABILITIES = \[([^\]]*)\]', source).group(1))
    assert SIGNED_COMMANDS_CAPABILITY in declared
    assert Heartbeat(capabilities=declared).capabilities == declared
