const test = require('node:test');
const assert = require('node:assert/strict');
const CloudMarketplaceProtocol = require('../cloud-marketplace-protocol');
globalThis.CloudMarketplaceProtocol = CloudMarketplaceProtocol;
const { createCloudConnection, ORIGIN } = require('../background/cloud-connection');
const { createApprovalVerifier } = require('../background/cloud-approvals');
const { approvalSigner } = require('./approval-signer');

const ALICE = '11111111-1111-4111-8111-111111111111';
const BOB = '22222222-2222-4222-8222-222222222222';
const REQUEST = '33333333-3333-4333-8333-333333333333';
const MESSAGE = 'a'.repeat(32);
const execute = (extra = {}) => ({ type: 'marketplace.execute', job_id: 'job-1', message_id: MESSAGE,
  payload: { marketplace: 'poshmark', action: 'list', confirm: true, operation_id: 'job-1', job_id: 'job-1', request_id: MESSAGE, ...extra } });
const photo = { id: '0f1e2d3c4b5a', mime_type: 'image/jpeg', base64: 'AAAA' };

test('sales history accepts bounded order ids and rejects arbitrary payloads', () => {
  const command = { type: 'marketplace.sales_history', job_id: null, message_id: MESSAGE,
    payload: { request_id: MESSAGE, marketplace: 'depop', settled: ['receipt-1'] } };
  assert.ok(CloudMarketplaceProtocol.command(command));
  for (const changes of [{ settled: ['https://example.com'] }, { settled: Array(2001).fill('id') },
    { marketplace: 'ebay' }, { url: 'https://example.com' }]) {
    assert.equal(CloudMarketplaceProtocol.command({ ...command, payload: { ...command.payload, ...changes } }), null);
  }
});

async function setup() {
  let data = {};
  let local = {};
  const runs = [];
  const signer = await approvalSigner();
  const api = {
    runtime: { id: 'bridge', getURL: path => 'chrome-extension://bridge/' + path, getManifest: () => ({ version: '0.2.40' }) },
    storage: {
      session: { get: async () => structuredClone(data), set: async value => { data = structuredClone(value); } },
      local: { get: async () => structuredClone(local), set: async value => { local = { ...local, ...structuredClone(value) }; } },
    },
    windows: { create: async () => ({ id: 9 }), remove: async () => {} },
    tabs: { get: async () => ({ url: ORIGIN + '/' }), sendMessage: async () => ({ current: true }) },
  };
  const bridge = createCloudConnection(api, { randomId: () => REQUEST,
    runCommand: async (command, photos) => { runs.push({ command, photos }); return { ok: true, state: 'published' }; },
    verifyApproval: createApprovalVerifier(api, { publicKey: signer.publicKey }).verify });
  const sender = { id: 'bridge', tab: { id: 7, url: ORIGIN + '/' }, url: ORIGIN + '/', frameId: 0, documentId: 'doc', origin: ORIGIN };
  const page = (action, accountId = ALICE, extra = {}) => bridge.handle({ type: 'cloud.connection', action, accountId,
    ...(action === 'connect' ? { email: 'alice@example.com' } : {}), ...extra }, sender);
  const connect = async () => {
    await page('connect');
    await bridge.handle({ type: 'cloud.connection', action: 'approve', requestId: REQUEST },
      { id: 'bridge', url: api.runtime.getURL('cloud-connect.html') + '#' + REQUEST });
  };
  return { api, page, connect, runs, sign: (command, account = ALICE) => signer.sign(command, account) };
}

test('an approved page relays the seller command with its photos and gets the Bridge reply', async () => {
  const s = await setup();
  await s.connect();
  assert.deepEqual((await s.page('status')).supportedActions, ['marketplace.actions', 'marketplace.sessions', 'marketplace.sales_history', 'marketplace.promotions', 'marketplace.import', 'vendoo.drafts', 'marketplace.signed_approvals']);
  const result = await s.page('marketplace_command', ALICE, { command: await s.sign(execute()), photos: [photo] });
  assert.deepEqual(result, { ok: true, reply: { ok: true, state: 'published' } });
  assert.equal(s.runs.length, 1);
  assert.equal(s.runs[0].command.payload.action, 'list');
  assert.equal(Object.hasOwn(s.runs[0].command.payload, 'approval'), false, 'the approval stays in the Bridge');
  assert.deepEqual(s.runs[0].photos, [photo]);
});

test('a write the page made up, edited or replayed never runs', async () => {
  const s = await setup();
  await s.connect();
  const refused = async command => (await s.page('marketplace_command', ALICE, { command, photos: [] })).reply;
  // The page's own confirm: true is not the seller's confirmation.
  assert.equal((await refused(execute())).approval, 'unsigned');
  const signed = await s.sign(execute());
  assert.equal((await refused({ ...signed, payload: { ...signed.payload, action: 'update', listing_id: 'b'.repeat(24) } })).approval, 'mismatch');
  assert.equal((await refused(await s.sign(execute(), BOB))).approval, 'mismatch');
  const remove = { type: 'marketplace.delete', job_id: 'job-1', message_id: MESSAGE,
    payload: { marketplace: 'depop', listing_id: 'some-listing', confirm: true, request_id: MESSAGE } };
  assert.equal((await refused(remove)).approval, 'unsigned');
  const offer = { type: 'marketplace.promote', job_id: 'job-1', message_id: MESSAGE, payload: { job_id: 'job-1', request_id: MESSAGE,
    marketplace: 'mercari', action: 'offer', listing_id: 'm12345678', action_id: 'action-1', confirm: true, price_cents: 2000,
    offer_price_cents: 1500, floor_cents: 1000, shipping_discount_id: null } };
  assert.equal((await refused(offer)).approval, 'unsigned');
  assert.equal(s.runs.length, 0);
  assert.deepEqual(await s.page('marketplace_command', ALICE, { command: await s.sign(offer), photos: [] }), { ok: true, reply: { ok: true, state: 'published' } });
  assert.deepEqual(await s.page('marketplace_command', ALICE, { command: signed, photos: [] }), { ok: true, reply: { ok: true, state: 'published' } });
  assert.equal((await refused(signed)).approval, 'replayed');
  assert.equal(s.runs.length, 2);
  // Reads need no approval.
  const sessions = { type: 'marketplace.sessions', job_id: null, message_id: MESSAGE, payload: { request_id: MESSAGE } };
  await s.page('marketplace_command', ALICE, { command: sessions, photos: [] });
  assert.equal(s.runs.length, 3);
});

test('a Bridge that relays writes cannot be built without the approval check', () => {
  assert.throws(() => createCloudConnection({}, { runCommand: async () => ({}) }), /approval check/);
});

test('commands fail closed without approval, for another account, or outside the contract', async () => {
  const s = await setup();
  assert.equal((await s.page('marketplace_command', ALICE, { command: execute(), photos: [] })).error_code, 'not_connected');
  await s.connect();
  assert.equal((await s.page('marketplace_command', BOB, { command: execute(), photos: [] })).error_code, 'not_connected');
  for (const command of [
    { ...execute(), type: 'browser.open' },
    execute({ marketplace: 'ebay' }),
    execute({ video: { id: 'v1' } }),
    { ...execute(), message_id: 'not-hex' },
    { ...execute(), extra: true },
  ]) {
    assert.equal((await s.page('marketplace_command', ALICE, { command, photos: [] })).error_code, 'invalid_input');
  }
  assert.equal((await s.page('marketplace_command', ALICE, { command: execute(), photos: [{ ...photo, mime_type: 'text/html' }] })).error_code, 'invalid_input');
  assert.equal((await s.page('marketplace_command', ALICE, { command: execute(), photos: [], access_token: 'x' })).error_code, 'not_connected');
  s.api.tabs.sendMessage = async () => ({ current: false });
  assert.equal((await s.page('marketplace_command', ALICE, { command: execute(), photos: [] })).error_code, 'account_changed');
  assert.equal(s.runs.length, 0);
});

test('cancel needs only the operation it stops', () => {
  const cancel = { type: 'job.cancel', job_id: 'op-1', message_id: MESSAGE, payload: { job_id: 'op-1', request_id: MESSAGE } };
  assert.ok(CloudMarketplaceProtocol.command(cancel));
  assert.equal(CloudMarketplaceProtocol.command({ ...cancel, payload: { job_id: 'op/../1', request_id: MESSAGE } }), null);
  assert.equal(CloudMarketplaceProtocol.reply('text'), null);
  assert.deepEqual(CloudMarketplaceProtocol.reply({ ok: false, error: 'x' }), { ok: false, error: 'x' });
});

test('reads take only their own inputs and no job', async () => {
  const read = (type, payload) => ({ type, job_id: null, message_id: MESSAGE, payload: { request_id: MESSAGE, ...payload } });
  assert.ok(CloudMarketplaceProtocol.command(read('marketplace.sessions', {})));
  assert.ok(CloudMarketplaceProtocol.command(read('marketplace.connect', { marketplace: 'poshmark' })));
  for (const marketplace of ['ebay', 'facebook', 'https://evil.example', '']) {
    assert.equal(CloudMarketplaceProtocol.command(read('marketplace.connect', { marketplace })), null, marketplace);
  }
  assert.ok(CloudMarketplaceProtocol.command(read('marketplace.import.page', { marketplace: 'depop' })));
  assert.ok(CloudMarketplaceProtocol.command(read('marketplace.import.page', { marketplace: 'mercari', cursor: 'next-1' })));
  assert.ok(CloudMarketplaceProtocol.command(read('marketplace.import.listing', { marketplace: 'poshmark', listing_id: 'b'.repeat(24) })));
  for (const [type, payload] of [
    ['marketplace.import.page', { marketplace: 'facebook' }],
    ['marketplace.import.page', { marketplace: 'ebay' }],
    ['marketplace.import.page', { marketplace: 'depop', cursor: 'x'.repeat(301) }],
    ['marketplace.import.page', { marketplace: 'depop', url: 'https://evil.example' }],
  ]) assert.equal(CloudMarketplaceProtocol.command(read(type, payload)), null, JSON.stringify(payload));
  for (const command of [
    read('marketplace.fetch', {}),
    read('marketplace.listing_state', { marketplace: 'facebook', listing_id: '1234567890' }),
  ]) assert.equal(CloudMarketplaceProtocol.command(command), null, JSON.stringify(command));
  const s = await setup();
  await s.connect();
  assert.deepEqual((await s.page('status')).supportedActions, ['marketplace.actions', 'marketplace.sessions', 'marketplace.sales_history', 'marketplace.promotions', 'marketplace.import', 'vendoo.drafts', 'marketplace.signed_approvals']);
});
