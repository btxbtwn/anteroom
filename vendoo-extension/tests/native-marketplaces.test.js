const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { loadWorker, call } = require('./worker-context');
const POSH = 'https://poshmark.com/edit-listing/6adc371572e62463d992ffef';
const MERCARI = 'https://www.mercari.com/sell/draft/testDraft/';

function setup(platform = 'poshmark') {
  const context = loadWorker();
  const stored = {};
  const actions = [];
  const replies = [];
  let url = platform === 'poshmark' ? POSH : MERCARI;
  let documentId = 0;
  context.chrome.storage.local.get = async key => ({ [key]: stored[key] });
  context.chrome.storage.local.set = async values => Object.assign(stored, values);
  context.chrome.tabs = {
    create: async options => { url = options.url; return { id: 4, url, status: 'complete' }; },
    get: async () => ({ id: 4, url, status: 'complete' }),
    update: async (_id, options) => { url = options.url; documentId++; return { id: 4, url, status: 'complete' }; },
  };
  context.chrome.scripting = { executeScript: async ({ args }) => {
    const [, action] = args;
    actions.push(action);
    if (action === 'find_draft') url = platform === 'poshmark' ? POSH : MERCARI;
    if (action === 'publish') url = platform === 'poshmark'
      ? 'https://poshmark.com/listing/Test-6adc371572e62463d992ffef' : 'https://www.mercari.com/us/item/m123456789/';
    return [{ result: action === 'inspect' ? { title: 'Test', description: 'Test description', price: '12.00', url, document_id: String(documentId) }
      : action === 'posh_read' ? { status: 'user_draft', price: 12, title: 'Test', description: 'Test description' }
      : action === 'publish' && platform === 'poshmark' ? { published: true, listing_url: url }
      : { ok: true, url } }];
  } };
  context.sendBrowserResult = (_msg, reply) => replies.push(reply);
  const payload = { operation_id: 'abc123', marketplace: platform, action: 'save_draft', draft_url: url,
    listing: { title: 'Test', description: 'Test description', price: 12 }, confirm: false };
  return { context, stored, actions, replies, msg: { type: 'marketplace.execute', job_id: 'abc123', payload } };
}

test('native links accept only the correct marketplace and durable draft route', () => {
  const c = loadWorker();
  assert.equal(call(c, 'nativeDraftUrl', 'poshmark', POSH), POSH);
  assert.equal(call(c, 'nativeDraftUrl', 'mercari', MERCARI), MERCARI);
  for (const url of [MERCARI, 'https://poshmark.com/create-listing', 'https://poshmark.com.evil/edit-listing/6adc371572e62463d992ffef']) {
    assert.equal(call(c, 'nativeDraftUrl', 'poshmark', url), '');
  }
});

test('an interrupted worker is never replayed, and checking a result does no page work', async () => {
  const s = setup();
  s.stored.native_operation_abc123 = { finished: false };
  await s.context.handleNativeMarketplaceMessage(s.msg);
  assert.equal(s.replies[0].ok, false);
  s.msg.type = 'marketplace.result';
  await s.context.handleNativeMarketplaceMessage(s.msg);
  assert.equal(s.replies[1].finished, false);
  assert.equal(s.actions.length, 0);
});

test('another running job blocks native work', async () => {
  const s = setup();
  vm.runInContext('activeJob = {job_id: "another"}', s.context);
  await s.context.handleNativeMarketplaceMessage(s.msg);
  assert.equal(s.replies[0].safe_to_retry, true);
  assert.equal(s.actions.length, 0);
});

test('Etsy listing writes are unavailable in the browser worker', async () => {
  const s = setup();
  for (const action of ['save_draft', 'publish']) {
    const result = await s.context.runNativeMarketplace({ payload: { ...s.msg.payload, marketplace: 'etsy', action, confirm: true } });
    assert.equal(result.ok, false);
    assert.match(result.error, /Unsupported/);
  }
  assert.equal(s.actions.length, 0);
  assert.equal(call(s.context, 'nativeDraftUrl', 'etsy', 'https://www.etsy.com/your/shops/me/listing-editor/edit/123456'), '');
});

test('checking an unchanged private Poshmark draft allows saving again without a mutation', async () => {
  const s = setup();
  Object.assign(s.context, { FormData, AbortSignal });
  const methods = [];
  s.context.fetch = async (_url, options) => {
    methods.push(options.method);
    return { ok: true, json: async () => ({ data: { status: 'user_draft', title: 'Test', description: 'Test description', price_amount: { val: '0' } } }) };
  };
  const result = await s.context.recoverNativeMarketplace(s.msg.payload);
  assert.equal(result.safe_to_retry, true);
  assert.deepEqual(methods, ['GET']);
});

test('legacy Poshmark fill messages cannot publish', () => {
  let listener;
  let clicks = 0;
  const c = { chrome: { runtime: { onMessage: { addListener: fn => { listener = fn; } } } },
    document: { querySelectorAll: () => [{ innerText: 'Next', click: () => { clicks++; } }] } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../content-scripts/poshmark.js'), 'utf8'), c);
  for (const type of ['START_FILL', 'START_POSHMARK']) listener({ type }, {}, reply => assert.equal(reply.ok, false));
  assert.equal(clicks, 0);
});

test('the controller never repeats Poshmark creation after a completed or interrupted API save', async () => {
  for (const fail of [false, true]) {
    const s = setup();
    s.msg.payload.draft_url = null;
    s.msg.payload.photos = [{ id: 'a1' }, { id: 'a2' }];
    let calls = 0;
    s.context.runPoshmarkApi = async () => {
      calls++;
      return fail ? { ok: false, safe_to_retry: false, error: 'Interrupted' }
        : { ok: true, state: 'draft', draft_url: POSH };
    };
    await s.context.handleNativeMarketplaceMessage(s.msg);
    assert.equal(s.replies[0].state, fail ? undefined : 'draft');
    if (fail) assert.equal(s.replies[0].safe_to_retry, false);
    await s.context.handleNativeMarketplaceMessage(s.msg);
    assert.equal(calls, 1);
  }
});

test('bridge reload targets only Vendoo and preserves native marketplace review tabs', async () => {
  const c = loadWorker();
  let patterns;
  const reloaded = [];
  c.chrome.tabs = {
    query: async options => { patterns = options.url; return [{ id: 4 }]; },
    reload: async id => { reloaded.push(id); },
  };
  await c.reloadVendooTabs();
  assert.ok(patterns.every(pattern => /vendoo\.co/.test(pattern)));
  assert.deepEqual(reloaded, [4]);
});
