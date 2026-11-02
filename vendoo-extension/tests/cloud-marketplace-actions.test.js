const test = require('node:test');
const assert = require('node:assert/strict');
const CloudMarketplaceProtocol = require('../cloud-marketplace-protocol');
globalThis.CloudMarketplaceProtocol = CloudMarketplaceProtocol;
const { createCloudConnection, ORIGIN } = require('../background/cloud-connection');

const ALICE = '11111111-1111-4111-8111-111111111111';
const BOB = '22222222-2222-4222-8222-222222222222';
const REQUEST = '33333333-3333-4333-8333-333333333333';
const MESSAGE = 'a'.repeat(32);
const execute = (extra = {}) => ({ type: 'marketplace.execute', job_id: 'job-1', message_id: MESSAGE,
  payload: { marketplace: 'poshmark', action: 'list', confirm: true, operation_id: 'job-1', job_id: 'job-1', request_id: MESSAGE, ...extra } });
const photo = { id: '0f1e2d3c4b5a', mime_type: 'image/jpeg', base64: 'AAAA' };

function setup() {
  let data = {};
  const runs = [];
  const api = {
    runtime: { id: 'bridge', getURL: path => 'chrome-extension://bridge/' + path, getManifest: () => ({ version: '0.2.40' }) },
    storage: { session: { get: async () => structuredClone(data), set: async value => { data = structuredClone(value); } } },
    windows: { create: async () => ({ id: 9 }), remove: async () => {} },
    tabs: { get: async () => ({ url: ORIGIN + '/' }), sendMessage: async () => ({ current: true }) },
  };
  const bridge = createCloudConnection(api, { randomId: () => REQUEST,
    runCommand: async (command, photos) => { runs.push({ command, photos }); return { ok: true, state: 'published' }; } });
  const sender = { id: 'bridge', tab: { id: 7, url: ORIGIN + '/' }, url: ORIGIN + '/', frameId: 0, documentId: 'doc', origin: ORIGIN };
  const page = (action, accountId = ALICE, extra = {}) => bridge.handle({ type: 'cloud.connection', action, accountId,
    ...(action === 'connect' ? { email: 'alice@example.com' } : {}), ...extra }, sender);
  const connect = async () => {
    await page('connect');
    await bridge.handle({ type: 'cloud.connection', action: 'approve', requestId: REQUEST },
      { id: 'bridge', url: api.runtime.getURL('cloud-connect.html') + '#' + REQUEST });
  };
  return { api, page, connect, runs };
}

test('an approved page relays the seller command with its photos and gets the Bridge reply', async () => {
  const s = setup();
  await s.connect();
  assert.deepEqual((await s.page('status')).supportedActions, ['marketplace.actions', 'vinted.choices']);
  const result = await s.page('marketplace_command', ALICE, { command: execute(), photos: [photo] });
  assert.deepEqual(result, { ok: true, reply: { ok: true, state: 'published' } });
  assert.equal(s.runs.length, 1);
  assert.equal(s.runs[0].command.payload.action, 'list');
  assert.deepEqual(s.runs[0].photos, [photo]);
});

test('commands fail closed without approval, for another account, or outside the contract', async () => {
  const s = setup();
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
  assert.ok(CloudMarketplaceProtocol.command(read('marketplace.vinted_categories', {})));
  assert.ok(CloudMarketplaceProtocol.command(read('marketplace.vinted_brands', { query: 'Nike', category: 'Women > Jeans' })));
  for (const command of [
    read('marketplace.vinted_categories', { url: 'https://evil.example' }),
    read('marketplace.vinted_brands', { query: 'x'.repeat(101) }),
    read('marketplace.vinted_brands', { query: 5 }),
    { ...read('marketplace.vinted_categories', {}), job_id: 'job-1' },
    { ...read('marketplace.vinted_categories', {}), version: 1 },
    read('marketplace.fetch', {}),
  ]) assert.equal(CloudMarketplaceProtocol.command(command), null, JSON.stringify(command));
  const s = setup();
  await s.connect();
  assert.deepEqual((await s.page('status')).supportedActions, ['marketplace.actions', 'vinted.choices']);
});
