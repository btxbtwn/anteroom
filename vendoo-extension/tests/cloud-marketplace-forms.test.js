const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker } = require('./worker-context');
const protocol = require('../cloud-marketplace-protocol');
const { createCloudConnection, ORIGIN } = require('../background/cloud-connection');
globalThis.CloudMarketplaceProtocol = protocol;
const ACCOUNT = '11111111-1111-4111-8111-111111111111';
const REQUEST = '22222222-2222-4222-8222-222222222222';
const FORM = { marketplace: 'poshmark', native: true, known: true, category_id: 'men/shirts',
  fields: [{ key: 'poshmark_specifics.title', label: 'Title', value: 'Coat', options: [], required: true, multi: false, selection_only: false, section: 'Title' }] };

async function connected(reader) {
  let records = {};
  const api = { runtime: { id: 'bridge', getURL: path => 'chrome-extension://bridge/' + path, getManifest: () => ({ version: '0.2.39' }) },
    storage: { session: { get: async () => structuredClone(records), set: async value => { records = structuredClone(value); } } },
    windows: { create: async () => ({ id: 9 }), remove: async () => {} },
    tabs: { get: async () => ({ url: ORIGIN + '/' }), sendMessage: async () => ({ current: true }) } };
  const bridge = createCloudConnection(api, { randomId: () => REQUEST, readForm: reader });
  const sender = { id: 'bridge', tab: { id: 7, url: ORIGIN + '/' }, url: ORIGIN + '/', origin: ORIGIN, frameId: 0, documentId: 'original' };
  const page = (action, extra = {}) => bridge.handle({ type: 'cloud.connection', action, accountId: ACCOUNT, ...extra }, sender);
  await page('connect', { email: 'seller@example.com' });
  await bridge.handle({ type: 'cloud.connection', action: 'approve', requestId: REQUEST }, { id: 'bridge', url: api.runtime.getURL('cloud-connect.html') });
  return { bridge, page, sender, api };
}

test('approved form reads reject arbitrary payloads and drop results after sign-out without blocking it', async () => {
  let finish, entered;
  const started = new Promise(resolve => { entered = resolve; });
  const s = await connected(() => { entered(); return new Promise(resolve => { finish = resolve; }); });
  assert.deepEqual((await s.page('status')).supportedActions, ['poshmark.form.read']);
  for (const listing of [{ access_token: 'private' }, { description: 'x'.repeat(20001) }, { price: Infinity }, { quantity: 0 }, { poshmark_specifics: {} }]) {
    assert.equal((await s.page('poshmark_form', { listing })).error_code, 'invalid_input');
  }
  const pending = s.page('poshmark_form', { listing: { title: 'Coat', price: 42 } });
  await started;
  assert.equal((await s.page('disconnect')).state, 'disconnected');
  finish({ ok: true, form: FORM });
  assert.deepEqual(await pending, { ok: false, error_code: 'account_changed' });
});

test('form reading requires the approved tab, frame, account and current document', async () => {
  let reads = 0;
  const s = await connected(async listing => { reads++; assert.equal(listing.title, 'Coat'); return { ok: true, form: FORM }; });
  const request = { type: 'cloud.connection', action: 'poshmark_form', accountId: ACCOUNT, listing: { title: 'Coat' } };
  for (const sender of [{ ...s.sender, frameId: 1 }, { ...s.sender, tab: { id: 8, url: ORIGIN + '/' } }, { ...s.sender, url: 'https://evil.example/' }]) {
    assert.equal((await s.bridge.handle(request, sender)).ok, false);
  }
  s.api.tabs.sendMessage = async () => ({ current: false });
  assert.equal((await s.bridge.handle(request, s.sender)).error_code, 'account_changed');
  assert.equal(reads, 0);
  s.api.tabs.sendMessage = async () => ({ current: true });
  assert.deepEqual(await s.bridge.handle(request, s.sender), { ok: true, form: FORM });
  assert.equal(reads, 1);
});

test('native reads reserve the existing exclusive operation scope and release it on error', async () => {
  const c = loadWorker();
  c.readPoshmarkEditorForm = async () => FORM;
  assert.equal((await c.readCloudPoshmarkForm({ title: 'Coat' })).ok, true);
  assert.equal(c.nativeOperationsRunning(), false);
  const operation = c.claimNativeOperation('depop', 'another-job');
  assert.equal((await c.readCloudPoshmarkForm({})).error_code, 'busy');
  c.releaseNativeOperation(operation);
  c.readPoshmarkEditorForm = async () => { throw { code: 'signed_out', message: 'PRIVATE diagnostic' }; };
  assert.deepEqual(JSON.parse(JSON.stringify(await c.readCloudPoshmarkForm({}))), { ok: false, error_code: 'signed_out' });
  assert.equal(c.nativeOperationsRunning(), false);
  vm.runInContext('activeJob = {job_id: "vendoo"}', c);
  assert.equal((await c.readCloudPoshmarkForm({})).error_code, 'busy');
});

test('form contract keeps native rows/layout and strips uncontracted private response data', () => {
  const clean = protocol.form({ ...FORM, authorization: 'PRIVATE', fields: [{ ...FORM.fields[0], width: 'half', headers: { cookie: 'PRIVATE' } }] });
  assert.equal(clean.fields[0].section, 'Title');
  assert.equal(clean.fields[0].width, 'half');
  assert.equal(JSON.stringify(clean).includes('PRIVATE'), false);
  assert.equal(protocol.form({ ...FORM, fields: [{ ...FORM.fields[0], key: 'poshmark_specifics.access_token' }] }), null);
  assert.equal(protocol.form({ ...FORM, marketplace: 'depop' }), null);
});

test('inspection holds the shared lock so the hosted reader cannot overlap it', async () => {
  const c = loadWorker(); let release, entered;
  const started = new Promise(resolve => { entered = resolve; });
  c.chrome.tabs = { query: async () => { entered(); return new Promise(resolve => { release = resolve; }); } };
  c.sendBrowserResult = () => {};
  const inspecting = c.handleMarketplaceInspection({ payload: { marketplace: 'poshmark' } });
  await started;
  assert.equal((await c.readCloudPoshmarkForm({})).error_code, 'busy');
  release([]); await inspecting;
  assert.equal(c.nativeOperationsRunning(), false);
});

test('disconnecting during the final document probe also discards the result', async () => {
  const s = await connected(async () => ({ ok: true, form: FORM }));
  let finish, entered; let calls = 0;
  const started = new Promise(resolve => { entered = resolve; });
  s.api.tabs.sendMessage = async () => {
    if (++calls === 1) return { current: true };
    entered(); return new Promise(resolve => { finish = resolve; });
  };
  const pending = s.page('poshmark_form', { listing: { title: 'Coat' } });
  await started; await s.page('disconnect'); finish({ current: true });
  assert.equal((await pending).error_code, 'account_changed');
});
