const test = require('node:test');
const assert = require('node:assert/strict');
const { createCloudConnection, ORIGIN } = require('../background/cloud-connection');
const ALICE = '11111111-1111-4111-8111-111111111111';
const BOB = '22222222-2222-4222-8222-222222222222';
const REQUEST = '33333333-3333-4333-8333-333333333333';
function setup() {
  let data = {};
  let time = 1000;
  const opened = [];
  const closed = [];
  const api = {
    runtime: { id: 'bridge', getURL: path => 'chrome-extension://bridge/' + path, getManifest: () => ({ version: '0.2.38' }) },
    storage: { session: { get: async () => structuredClone(data), set: async value => { data = structuredClone(value); } } },
    windows: { create: async options => { opened.push(options); return { id: 9 }; }, remove: async id => closed.push(id) },
    tabs: { get: async () => ({ url: ORIGIN + '/' }), sendMessage: async () => ({ current: true }) },
  };
  const bridge = createCloudConnection(api, { now: () => time, randomId: () => REQUEST });
  const sender = { id: 'bridge', tab: { id: 7, url: ORIGIN + '/' }, url: ORIGIN + '/', frameId: 0, documentId: 'original-document', origin: ORIGIN };
  const popup = { id: 'bridge', url: api.runtime.getURL('cloud-connect.html') + '#' + REQUEST };
  const page = (action, accountId = ALICE, extra = {}) => bridge.handle({ type: 'cloud.connection', action, accountId,
    ...(action === 'connect' ? { email: accountId === ALICE ? 'alice@example.com' : 'bob@example.com' } : {}), ...extra }, sender);
  const approval = action => bridge.handle({ type: 'cloud.connection', action, requestId: REQUEST }, popup);
  return { bridge, api, sender, popup, page, approval, opened, closed, advance: value => { time += value; }, records: () => data };
}

test('connection requires approval from the extension page and targets the requesting document', async () => {
  const s = setup();
  assert.equal((await s.page('status')).state, 'disconnected');
  assert.equal((await s.page('connect')).state, 'pending');
  assert.equal(s.opened.length, 1);
  await s.page('connect');
  assert.equal(s.opened.length, 1, 'duplicate clicks reuse pending approval');
  assert.deepEqual(await s.bridge.handle({ type: 'cloud.connection', action: 'approve', requestId: REQUEST }, s.sender), { ok: false });
  assert.equal((await s.page('status')).state, 'pending');
  assert.equal((await s.approval('details')).email, 'alice@example.com');
  let probe;
  s.api.tabs.sendMessage = async (...args) => { probe = args; return { current: true }; };
  assert.deepEqual(await s.approval('approve'), { ok: true });
  assert.deepEqual(probe, [7, { type: 'cloud.connection.probe', accountId: ALICE }, { documentId: 'original-document' }]);
  const connected = await s.page('status');
  assert.equal(connected.state, 'connected');
  assert.deepEqual(connected.supportedActions, []);
  assert.equal(JSON.stringify(connected).includes('alice'), false);
});

test('wrong origin, embedded frames, foreign extensions and credential payloads fail closed', async () => {
  const s = setup();
  const request = { type: 'cloud.connection', action: 'connect', accountId: ALICE, email: 'alice@example.com' };
  for (const sender of [{ ...s.sender, id: 'foreign' }, { ...s.sender, frameId: 1 }, { ...s.sender, origin: 'https://evil.example' },
    { ...s.sender, url: 'https://evil.example/' }, { ...s.sender, tab: { id: 7, url: 'https://evil.example' } }, { ...s.sender, documentId: undefined }]) {
    assert.deepEqual(await s.bridge.handle(request, sender), { ok: false });
  }
  assert.deepEqual(await s.page('connect', ALICE, { access_token: 'must-not-be-forwarded' }), { ok: false });
  assert.deepEqual(await s.page('publish', ALICE, { confirm: true }), { ok: false });
  assert.equal(s.opened.length, 0);
  assert.deepEqual(s.records(), {});
});

test('another account never receives the previous connection and late sign-out cannot disconnect its replacement', async () => {
  const s = setup();
  await s.page('connect'); await s.approval('approve');
  assert.equal((await s.page('status', BOB)).state, 'disconnected');
  await s.page('connect', BOB); await s.approval('approve');
  await s.page('disconnect', ALICE);
  assert.equal((await s.page('status', BOB)).state, 'connected');
  await s.page('disconnect', BOB);
  assert.equal((await s.page('status', BOB)).state, 'disconnected');
});

test('expired, closed, navigated and changed-account approval requests cannot connect', async () => {
  for (const invalidate of [
    async s => s.advance(120001),
    async s => s.bridge.closedApproval(9),
    async s => s.bridge.navigated(7, { url: 'https://evil.example' }),
    async s => { s.api.tabs.sendMessage = async () => ({ current: false }); },
    async s => { s.api.tabs.sendMessage = async () => { throw new Error('Document no longer exists'); }; },
    async s => s.bridge.clear(7),
  ]) {
    const s = setup(); await s.page('connect'); await invalidate(s);
    assert.equal((await s.approval('approve')).ok, false);
    assert.equal((await s.page('status')).state, 'disconnected');
  }
});

test('simultaneous tabs retain independent session records across worker reconstruction', async () => {
  const s = setup();
  const second = { ...s.sender, tab: { id: 8, url: ORIGIN + '/' } };
  await Promise.all([s.page('connect'), s.bridge.handle({ type: 'cloud.connection', action: 'connect', accountId: BOB, email: 'bob@example.com' }, second)]);
  assert.equal(Object.keys(s.records().anteroom_cloud_connections).length, 2);
  const restored = createCloudConnection(s.api, { now: () => 1000 });
  assert.equal((await restored.handle({ type: 'cloud.connection', action: 'status', accountId: ALICE }, s.sender)).state, 'pending');
});
