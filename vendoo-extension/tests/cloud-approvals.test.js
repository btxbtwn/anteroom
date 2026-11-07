const test = require('node:test');
const assert = require('node:assert/strict');
const { createApprovalVerifier, canonical, BRIDGE_APPROVAL_PUBLIC_KEY } = require('../background/cloud-approvals');
const { approvalSigner } = require('./approval-signer');

const ALICE = '11111111-1111-4111-8111-111111111111';
const BOB = '22222222-2222-4222-8222-222222222222';
const MESSAGE = 'a'.repeat(32);
const NOW = 1_800_000_000;
const update = (extra = {}) => ({ type: 'marketplace.execute', job_id: 'job-1', message_id: MESSAGE,
  payload: { marketplace: 'poshmark', action: 'update', listing_id: 'b'.repeat(24), confirm: true, price: 25,
    operation_id: 'job-1', request_id: MESSAGE, ...extra } });

function storage() {
  let data = {};
  return { local: { get: async key => ({ [key]: structuredClone(data[key]) }), set: async value => { data = { ...data, ...structuredClone(value) }; } } };
}

async function setup({ publicKey, seconds = NOW } = {}) {
  const signer = await approvalSigner();
  let time = seconds * 1000;
  const verifier = createApprovalVerifier({ storage: storage() }, { publicKey: publicKey ?? signer.publicKey, now: () => time });
  return { signer, verify: verifier.verify, advance: s => { time += s * 1000; } };
}

test('a signed write for this account and command runs once', async () => {
  const s = await setup();
  const command = await s.signer.sign(update(), ALICE, { seconds: NOW });
  assert.equal(await s.verify(command, ALICE), null);
  const again = await s.verify(command, ALICE);
  assert.equal(again.approval, 'replayed');
  assert.equal(again.ok, false);
  assert.equal(again.safe_to_retry, true);
});

test('the account id compares without case, as Supabase and the page may differ', async () => {
  const s = await setup();
  assert.equal(await s.verify(await s.signer.sign(update(), ALICE.toUpperCase(), { seconds: NOW }), ALICE), null);
});

test('an expired approval, or one from the future, is refused', async () => {
  const s = await setup();
  const old = await s.signer.sign(update(), ALICE, { seconds: NOW - 300 - 61 });
  assert.equal((await s.verify(old, ALICE)).approval, 'expired');
  const early = await s.signer.sign(update(), ALICE, { seconds: NOW + 120 });
  assert.equal((await s.verify(early, ALICE)).approval, 'expired');
  const late = await s.signer.sign(update(), ALICE, { seconds: NOW });
  s.advance(300 + 61);
  assert.equal((await s.verify(late, ALICE)).approval, 'expired');
});

test('an approval for another account, command, marketplace, action, listing or job is refused', async () => {
  const s = await setup();
  for (const changes of [{ sub: BOB }, { cmd: 'marketplace.delete' }, { mkt: 'depop' }, { act: 'list' },
    { lid: 'c'.repeat(24) }, { job: 'job-2' }, { mid: 'b'.repeat(32) }]) {
    const command = await s.signer.sign(update(), ALICE, { seconds: NOW, changes });
    assert.equal((await s.verify(command, ALICE)).approval, 'mismatch', JSON.stringify(changes));
  }
  const signed = await s.signer.sign(update(), ALICE, { seconds: NOW });
  assert.equal((await s.verify(signed, BOB)).approval, 'mismatch');
});

test('a page that edits a signed command is refused', async () => {
  const s = await setup();
  const signed = await s.signer.sign(update(), ALICE, { seconds: NOW });
  for (const changed of [
    { ...signed, payload: { ...signed.payload, price: 1 } },
    { ...signed, payload: { ...signed.payload, listing_id: 'c'.repeat(24) } },
    { ...signed, payload: { ...signed.payload, marketplace: 'depop' } },
    { ...signed, type: 'marketplace.delete' },
  ]) assert.match((await s.verify(changed, ALICE)).approval, /mismatch/);
});

test('an unsigned write, a forged signature or a malformed token is refused', async () => {
  const s = await setup();
  assert.equal((await s.verify(update(), ALICE)).approval, 'unsigned');
  const other = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const forged = await s.signer.sign(update(), ALICE, { seconds: NOW, privateKey: other.privateKey });
  assert.equal((await s.verify(forged, ALICE)).approval, 'invalid');
  for (const approval of ['x', 'a.b.c', 'eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIn0.' + 'A'.repeat(86)]) {
    assert.equal((await s.verify(update({ approval }), ALICE)).approval, 'invalid', approval);
  }
  const long = await s.signer.sign(update(), ALICE, { seconds: NOW, changes: { exp: NOW + 3600 } });
  assert.equal((await s.verify(long, ALICE)).approval, 'invalid');
});

test('without a configured key every write is refused, and reads still run', async () => {
  const s = await setup({ publicKey: '' });
  const signed = await s.signer.sign(update(), ALICE, { seconds: NOW });
  const refusal = await s.verify(signed, ALICE);
  assert.equal(refusal.approval, 'no_key');
  assert.match(refusal.error, /no approval key/);
  const read = { type: 'marketplace.sessions', job_id: null, message_id: MESSAGE, payload: { request_id: MESSAGE } };
  assert.equal(await s.verify(read, ALICE), null);
  assert.equal(typeof BRIDGE_APPROVAL_PUBLIC_KEY, 'string');
});

test('every write the hosted API relays needs an approval; reads and cancel do not', async () => {
  const s = await setup();
  for (const type of ['job.vendoo_api', 'marketplace.execute', 'marketplace.result', 'marketplace.delete', 'marketplace.promote']) {
    assert.equal((await s.verify({ type, job_id: 'job-1', message_id: MESSAGE, payload: { request_id: MESSAGE } }, ALICE)).approval, 'unsigned', type);
  }
  for (const type of ['job.cancel', 'marketplace.promotion_check', 'marketplace.promotion_result', 'marketplace.like_events',
    'marketplace.sessions', 'marketplace.connect', 'marketplace.import.page', 'marketplace.import.listing', 'marketplace.sales_history']) {
    assert.equal(await s.verify({ type, job_id: null, message_id: MESSAGE, payload: { request_id: MESSAGE } }, ALICE), null, type);
  }
});

test('used approvals are forgotten once they expire', async () => {
  const s = await setup();
  const store = storage();
  let time = NOW * 1000;
  const verifier = createApprovalVerifier({ storage: store }, { publicKey: s.signer.publicKey, now: () => time });
  assert.equal(await verifier.verify(await s.signer.sign(update(), ALICE, { seconds: NOW }), ALICE), null);
  time += 400 * 1000;
  assert.equal(await verifier.verify(await s.signer.sign(update(), ALICE, { seconds: NOW + 400 }), ALICE), null);
  const used = (await store.local.get('anteroom_used_approvals')).anteroom_used_approvals;
  assert.equal(Object.keys(used).length, 1);
});

test('two copies of one approval racing each other run once', async () => {
  const s = await setup();
  const command = await s.signer.sign(update(), ALICE, { seconds: NOW });
  const results = await Promise.all([s.verify(command, ALICE), s.verify(command, ALICE), s.verify(command, ALICE)]);
  assert.equal(results.filter(result => result === null).length, 1);
});

test('the canonical form matches the server for the values a command carries', () => {
  // The same strings services/bridge_approvals.py writes (test_bridge_approvals.py checks the Python side).
  assert.equal(canonical({ b: [1, 2.5, null, true], a: 'é"\n', c: { z: 0.1, y: 1e21, x: 1e-7, w: -0 } }),
    '{"a":"é\\"\\n","b":[1,2.5,null,true],"c":{"w":0,"x":1e-7,"y":1e+21,"z":0.1}}');
});
