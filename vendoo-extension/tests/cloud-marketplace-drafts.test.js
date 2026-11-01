// Save draft from the web app: the page relays a server-signed command; the
// content script checks its shape, the worker checks the connection and the
// signature, runs the native save with the hosted photo source, and reports.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { loadWorker } = require('./worker-context');
const { createCloudConnection, ORIGIN } = require('../background/cloud-connection');
const protocol = require('../cloud-marketplace-protocol');

const ALICE = '11111111-1111-4111-8111-111111111111';
const BOB = '22222222-2222-4222-8222-222222222222';
const REQUEST = '33333333-3333-4333-8333-333333333333';
const TOKEN = 'T'.repeat(43);
const FIXTURE = require('./fixtures/cloud-signed-command.json');

const b64 = bytes => Buffer.from(bytes).toString('base64url');

async function signer() {
  const pair = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const { kty, crv, x, y } = await crypto.subtle.exportKey('jwk', pair.publicKey);
  async function sign(command) {
    const body = b64(new TextEncoder().encode(JSON.stringify(command)));
    const signature = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, pair.privateKey, new TextEncoder().encode(body));
    return { body, signature: b64(new Uint8Array(signature)) };
  }
  return { key: { kty, crv, x, y }, sign };
}

function draftCommand(overrides = {}, payload = {}) {
  return {
    version: 1, kind: 'marketplace.save_draft', user_id: ALICE, operation_id: 'a1b2c3d4e5f6',
    expires_at: Math.floor(Date.now() / 1000) + 300, token: TOKEN, ...overrides,
    payload: { marketplace: 'poshmark', action: 'save_draft', confirm: false, operation_id: overrides.operation_id || 'a1b2c3d4e5f6',
      draft_url: null, listing_id: null, listing: { title: 'Coat' }, local_draft_id: null, revision_id: 'r1',
      photos: [{ id: 'a0a0a0a0a0a0', mime_type: 'image/jpeg' }], ...payload },
  };
}

function worker() {
  const context = loadWorker();
  const store = {};
  context.chrome.storage.local.get = async key => (key in store ? { [key]: structuredClone(store[key]) } : {});
  context.chrome.storage.local.set = async values => { Object.assign(store, structuredClone(values)); };
  context.__store = store;
  return context;
}

const run = (context, expression, values = {}) => {
  Object.assign(context, values);
  return vm.runInContext(expression, context);
};

test('a Python-signed command verifies in the worker, byte for byte', async () => {
  const context = worker();
  const verified = await run(context, 'verifyCloudCommand(__relayed, __account, [__key], 0)',
    { __relayed: FIXTURE.command, __account: ALICE, __key: FIXTURE.public_key });
  assert.deepEqual(JSON.parse(JSON.stringify(verified)), FIXTURE.decoded);
  // Signed for Alice: Bob's connection cannot run it.
  assert.equal(await run(context, 'verifyCloudCommand(__relayed, __bob, [__key], 0)', { __bob: BOB }), null);
});

test('the worker accepts only a current save_draft signed by a trusted key for this account', async () => {
  const context = worker();
  const trusted = await signer();
  const stranger = await signer();
  const check = async (relayed, account = ALICE, keys = [trusted.key]) =>
    run(context, 'verifyCloudCommand(__relayed, __account, __keys)', { __relayed: relayed, __account: account, __keys: keys });

  assert.equal((await check(await trusted.sign(draftCommand()))).operation_id, 'a1b2c3d4e5f6');
  assert.equal(await check(await stranger.sign(draftCommand())), null, 'unknown key');
  assert.equal(await check(await trusted.sign(draftCommand()), ALICE, []), null, 'no key shipped');
  const good = await trusted.sign(draftCommand());
  assert.equal(await check({ ...good, body: good.body.slice(0, -2) + 'AA' }), null, 'altered body');
  assert.equal(await check(good, BOB), null, 'another account');
  for (const [overrides, payload] of [
    [{ expires_at: Math.floor(Date.now() / 1000) - 1 }, {}],
    [{ kind: 'marketplace.list' }, {}],
    [{ version: 2 }, {}],
    [{}, { action: 'list' }],
    [{}, { action: 'publish' }],
    [{}, { confirm: true }],
    [{}, { marketplace: 'ebay' }],
    [{}, { marketplace: 'vinted' }],
    [{}, { operation_id: 'ffffffffffff' }],
    [{}, { video: { id: 'v1' } }],
    [{}, { photos: [] }],
    [{ token: 'short' }, {}],
  ]) {
    assert.equal(await check(await trusted.sign(draftCommand(overrides, payload))), null, JSON.stringify([overrides, payload]));
  }
});

test('a verified save reads photos from the hosted operation with its token and reports the result', async () => {
  const context = worker();
  const requests = [];
  context.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method || 'GET', auth: options.headers?.Authorization, body: options.body });
    if (url.includes('/photos/')) return new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { 'content-type': 'image/jpeg' } });
    return new Response('{}', { status: 200 });
  };
  let held;
  context.runPoshmarkApi = async msg => {
    // The marketplace scope is held while it runs; another marketplace is free.
    held = [vm.runInContext("claimNativeOperation('poshmark', 'other')", context),
      vm.runInContext("claimNativeOperation('mercari', 'other')", context)];
    vm.runInContext('releaseNativeOperation(__held)', Object.assign(context, { __held: held[1] }));
    const photo = await vm.runInContext('nativePhoto', context)(msg.job_id, msg.payload.photos[0]);
    assert.equal(photo.base64, 'AQID');
    assert.equal((msg.payload.action), 'save_draft');
    return { ok: true, state: 'draft', draft_url: 'https://poshmark.com/edit-listing/0123456789abcdef01234567', private: 'x' };
  };
  const result = await run(context, 'runCloudSaveDraft(__command)', { __command: draftCommand() });
  assert.equal(held[0], null);
  assert.notEqual(held[1], null);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    ok: true, state: 'draft', draft_url: 'https://poshmark.com/edit-listing/0123456789abcdef01234567', recorded: true });
  assert.deepEqual(requests.map(({ url, method, auth }) => [url, method, auth]), [
    [`${ORIGIN}/api/operations/a1b2c3d4e5f6/photos/a0a0a0a0a0a0`, 'GET', `Bearer ${TOKEN}`],
    [`${ORIGIN}/api/operations/a1b2c3d4e5f6/result`, 'POST', `Bearer ${TOKEN}`],
  ]);
  assert.deepEqual(JSON.parse(requests[1].body), { result: {
    ok: true, state: 'draft', draft_url: 'https://poshmark.com/edit-listing/0123456789abcdef01234567' } });
  // Journaled like the Mac's: the same command again returns that result and saves nothing new.
  context.runPoshmarkApi = async () => { throw new Error('must not run twice'); };
  const again = await run(context, 'runCloudSaveDraft(__command)');
  assert.equal(again.ok, true);
  // The photo source ends with the operation: the loopback path is back for the Mac.
  assert.equal(vm.runInContext('nativeOperationSources.size', context), 0);
});

test('a busy marketplace refuses safely, and a payload never turns into a publish', async () => {
  const context = worker();
  const reported = [];
  context.fetch = async (url, options = {}) => { reported.push(JSON.parse(options.body)); return new Response('{}', { status: 200 }); };
  vm.runInContext("__busy = claimNativeOperation('depop', 'mac-job')", context);
  const busy = await run(context, 'runCloudSaveDraft(__command)', { __command: draftCommand({}, { marketplace: 'depop' }) });
  assert.equal(busy.ok, false);
  assert.equal(reported[0].result.safe_to_retry, true);
  assert.match(reported[0].result.error, /Another Depop action/);
  vm.runInContext('releaseNativeOperation(__busy)', context);

  let action;
  context.runMercariNative = async msg => { action = [msg.payload.action, msg.payload.confirm]; return { ok: true, state: 'draft', draft_url: 'https://www.mercari.com/sell/draft/abc/' }; };
  await run(context, 'runCloudSaveDraft(__command)', { __command: draftCommand({ operation_id: 'bbbbbbbbbbbb' }, { marketplace: 'mercari', action: 'publish', confirm: true }) });
  assert.deepEqual(action, ['save_draft', false]);
});

function connection({ available = true } = {}) {
  let data = {};
  const calls = [];
  const api = {
    runtime: { id: 'bridge', getURL: p => 'chrome-extension://bridge/' + p, getManifest: () => ({ version: '0.2.40' }) },
    storage: { session: { get: async () => structuredClone(data), set: async value => { data = structuredClone(value); } } },
    windows: { create: async () => ({ id: 9 }), remove: async () => {} },
    tabs: { get: async () => ({ url: ORIGIN + '/' }), sendMessage: async () => ({ current: true }) },
  };
  const drafts = {
    available: () => available,
    verify: async (command, account) => { calls.push(['verify', account]); return command?.body === 'good' && account === ALICE ? { operation_id: 'a1b2c3d4e5f6' } : null; },
    refuse: async (command, code) => { calls.push(['refuse', code]); return true; },
    run: async () => { calls.push(['run']); return { ok: true, recorded: true }; },
  };
  const bridge = createCloudConnection(api, { randomId: () => REQUEST, drafts });
  const sender = { id: 'bridge', tab: { id: 7, url: ORIGIN + '/' }, url: ORIGIN + '/', frameId: 0, documentId: 'doc', origin: ORIGIN };
  const page = (action, extra = {}, from = sender) => bridge.handle({ type: 'cloud.connection', action, accountId: ALICE, ...extra }, from);
  const approve = async () => {
    await page('connect', { email: 'alice@example.com' });
    await bridge.handle({ type: 'cloud.connection', action: 'approve', requestId: REQUEST }, { id: 'bridge', url: api.runtime.getURL('cloud-connect.html') });
  };
  return { bridge, api, page, approve, calls, sender };
}

test('the worker runs a save only for an approved tab of the same account, and says when it can', async () => {
  const s = connection();
  const command = { body: 'good', signature: 'x' };
  assert.deepEqual((await s.page('status')).supportedActions, ['marketplace.save_draft']);
  assert.deepEqual((await connection({ available: false }).page('status')).supportedActions, []);

  // Not approved: a verified command is refused and reported, never run.
  assert.deepEqual(await s.page('marketplace_save_draft', { command }), { ok: false, error_code: 'not_connected', recorded: true });
  assert.deepEqual(s.calls, [['verify', ALICE], ['refuse', 'not_connected']]);
  await s.approve();
  s.calls.length = 0;
  // Bad signature, extra keys, other frames and foreign extensions never reach run.
  assert.deepEqual(await s.page('marketplace_save_draft', { command: { body: 'forged', signature: 'x' } }), { ok: false, error_code: 'invalid_command' });
  assert.deepEqual(await s.page('marketplace_save_draft', { command, url: 'https://evil.example' }), { ok: false, error_code: 'not_connected' });
  assert.deepEqual(await s.page('marketplace_save_draft', { command }, { ...s.sender, frameId: 1 }), { ok: false, error_code: 'not_connected' });
  assert.deepEqual(await s.page('marketplace_save_draft', { command }, { ...s.sender, id: 'foreign' }), { ok: false, error_code: 'not_connected' });
  assert.equal(s.calls.some(([name]) => name === 'run'), false);
  // The page reloaded or signed out: the original document no longer answers.
  s.api.tabs.sendMessage = async () => ({ current: false });
  assert.equal((await s.page('marketplace_save_draft', { command })).error_code, 'account_changed');
  s.api.tabs.sendMessage = async () => ({ current: true });
  assert.deepEqual(await s.page('marketplace_save_draft', { command }), { ok: true, recorded: true });
  assert.deepEqual(s.calls.at(-1), ['run']);
});

const contentCode = fs.readFileSync(path.join(__dirname, '../content-scripts/cloud-connection.js'), 'utf8');
function content(reply = { ok: true, recorded: true, state: 'draft', private: 'x' }) {
  let listener;
  const sent = [], replies = [];
  const window = { addEventListener: (_, fn) => { listener = fn; }, postMessage: (...args) => replies.push(args) };
  window.top = window;
  const chrome = { runtime: { id: 'bridge', onMessage: { addListener() {} },
    sendMessage: async msg => { sent.push(msg); return msg.action === 'status' ? { ok: true, state: 'connected', supportedActions: ['marketplace.save_draft', 'other'] } : reply; } } };
  vm.runInNewContext(contentCode, { window, location: { origin: ORIGIN }, chrome, CloudMarketplaceProtocol: protocol });
  const emit = data => listener({ source: window, origin: ORIGIN, data: { source: 'anteroom-cloud', version: 1, requestId: REQUEST, accountId: ALICE, ...data } });
  return { sent, replies, emit };
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const RELAYED = { body: 'eyJ2IjoxfQ', signature: 'A'.repeat(86) };

test('the content script relays only a well-formed command for the connected account', async () => {
  const s = content();
  s.emit({ action: 'status' }); await settle();
  assert.deepEqual([...s.replies[0][0].supportedActions], ['marketplace.save_draft']);
  s.emit({ action: 'marketplace_save_draft', command: RELAYED }); await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(s.sent.at(-1))), { type: 'cloud.connection', action: 'marketplace_save_draft', accountId: ALICE, command: RELAYED });
  assert.deepEqual(JSON.parse(JSON.stringify(s.replies.at(-1)[0])), { source: 'anteroom-extension', version: 1, requestId: REQUEST, ok: true, recorded: true });
  const before = s.sent.length;
  for (const bad of [
    { command: { ...RELAYED, url: 'https://evil.example' } },
    { command: { body: RELAYED.body } },
    { command: { ...RELAYED, signature: 'short' } },
    { command: { ...RELAYED, body: 'not base64url!' } },
    { command: RELAYED, confirm: true },
    { command: RELAYED, listing: {} },
  ]) s.emit({ action: 'marketplace_save_draft', ...bad });
  await settle();
  assert.equal(s.sent.length, before);
  // Another account in the page: answered here, never sent.
  s.emit({ action: 'marketplace_save_draft', command: RELAYED, accountId: BOB }); await settle();
  assert.equal(s.sent.length, before);
  assert.equal(s.replies.at(-1)[0].error_code, 'account_changed');
});

test('the content script returns only whether the save worked and was recorded', async () => {
  const s = content({ ok: false, error_code: 'secret-detail', error: 'cookie=1', recorded: false });
  s.emit({ action: 'status' }); await settle();
  s.emit({ action: 'marketplace_save_draft', command: RELAYED }); await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(s.replies.at(-1)[0])), { source: 'anteroom-extension', version: 1, requestId: REQUEST, ok: false, recorded: false, error_code: 'unavailable' });
});

test('the hosted origin is a host permission, so the worker can read photos and report', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../manifest.json'), 'utf8'));
  assert.ok(manifest.host_permissions.includes(ORIGIN + '/*'));
  const key = fs.readFileSync(path.join(__dirname, '../cloud-command-key.js'), 'utf8');
  assert.match(key, /^var CLOUD_COMMAND_KEYS = \[/m);
});
