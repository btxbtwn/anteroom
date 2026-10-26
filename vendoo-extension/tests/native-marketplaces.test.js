const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { loadWorker, call, reply } = require('./worker-context');
const MERCARI = 'https://www.mercari.com/sell/draft/testDraft/';

function setup(platform = 'mercari') {
  const context = loadWorker();
  const stored = {};
  const actions = [];
  const replies = [];
  let url = MERCARI;
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
    if (action === 'find_draft') url = MERCARI;
    if (action === 'publish') url = 'https://www.mercari.com/us/item/m123456789/';
    return [{ result: action === 'inspect' ? { title: 'Test', description: 'Test description', price: '12.00', url, document_id: String(documentId) }
      : action === 'posh_read' ? { status: 'user_draft', price: 12, title: 'Test', description: 'Test description' }
      : { ok: true, url } }];
  } };
  context.sendBrowserResult = (_msg, reply) => replies.push(reply);
  const payload = { operation_id: 'abc123', marketplace: platform, action: 'save_draft', draft_url: url,
    listing: { title: 'Test', description: 'Test description', price: 12 }, confirm: false };
  return { context, stored, actions, replies, msg: { type: 'marketplace.execute', job_id: 'abc123', payload } };
}

test('native links accept only the correct marketplace and durable draft route', () => {
  const c = loadWorker();
  assert.equal(call(c, 'nativeDraftUrl', 'mercari', MERCARI), MERCARI);
  for (const url of ['https://poshmark.com/edit-listing/6adc371572e62463d992ffef', 'https://www.mercari.com.evil/sell/draft/testDraft/']) {
    assert.equal(call(c, 'nativeDraftUrl', 'mercari', url), '');
  }
  assert.equal(call(c, 'nativeDraftUrl', 'poshmark', 'https://poshmark.com/edit-listing/6adc371572e62463d992ffef'), '');
  assert.equal(call(c, 'nativeDraftUrl', 'vinted', 'https://www.vinted.com/items/42/edit'), 'https://www.vinted.com/items/42/edit');
  assert.equal(call(c, 'nativeDraftUrl', 'vinted', 'https://www.vinted.com/items/42/edit/'), 'https://www.vinted.com/items/42/edit/');
  assert.equal(call(c, 'nativeDraftUrl', 'vinted', 'https://www.vinted.com/items/42'), '');
  assert.equal(call(c, 'nativeDraftUrl', 'vinted', 'https://www.vinted.com/items/42-tee/edit'), '');
});

test('an interrupted worker is never replayed, and checking a result does no page work', async () => {
  const s = setup();
  s.stored.native_operation_abc123 = { finished: false };
  await s.context.handleNativeMarketplaceMessage(s.msg);
  assert.equal(s.replies[0].ok, false);
  assert.equal(s.replies[0].safe_to_retry, false);
  assert.equal(s.replies[0].error_code, 'uncertain');
  s.msg.type = 'marketplace.result';
  await s.context.handleNativeMarketplaceMessage(s.msg);
  assert.equal(s.replies[1].finished, false);
  assert.equal(s.actions.length, 0);
});

test('a finished Depop publish without a link reads the public product URL', async () => {
  const s = setup('depop');
  const draft = 'https://www.depop.com/sellinghub/drafts/edit/123456/';
  s.stored.native_operation_abc123 = {
    finished: true, publish_job_id: 'job-1',
    context: { marketplace: 'depop', action: 'publish', draft_url: draft },
    result: { ok: true, state: 'published', draft_url: draft },
  };
  s.context.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  s.context.fetch = async url => {
    const { pathname } = new URL(url);
    if (pathname === '/api/v3/drafts/products/job-1/status/') {
      return reply(200, { count_by_status: { COMPLETE: 1, PENDING: 0 }, products: [{ slug: 'seller-columbia-xl', draft_id: '123456' }] });
    }
    throw new Error(pathname);
  };
  s.msg.type = 'marketplace.result';
  s.msg.payload = { operation_id: 'abc123', context: { marketplace: 'depop', listing: { title: 'Test', description: 'Private draft', price: 12 } } };
  await s.context.handleNativeMarketplaceMessage(s.msg);
  assert.equal(s.replies[0].result.listing_url, 'https://www.depop.com/products/seller-columbia-xl/');
  assert.equal(s.stored.native_operation_abc123.result.listing_url, 'https://www.depop.com/products/seller-columbia-xl/');
});

test('another running job blocks native work', async () => {
  const s = setup();
  vm.runInContext('activeJob = {job_id: "another"}', s.context);
  await s.context.handleNativeMarketplaceMessage(s.msg);
  assert.equal(s.replies[0].safe_to_retry, true);
  assert.equal(s.replies[0].error_code, 'rejected');
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

test('Poshmark only lists in one confirmed step', async () => {
  const s = setup();
  for (const action of ['save_draft', 'publish']) {
    const result = await s.context.runNativeMarketplace({ payload: { ...s.msg.payload, marketplace: 'poshmark', action, confirm: true } });
    assert.match(result.error, /Unsupported/);
  }
  const unconfirmed = await s.context.runNativeMarketplace({ payload: { ...s.msg.payload, marketplace: 'poshmark', action: 'list', confirm: false } });
  assert.match(unconfirmed.error, /Confirm/);
  const missing = await s.context.runNativeMarketplace({ payload: { ...s.msg.payload, marketplace: 'poshmark', action: 'update', confirm: true } });
  assert.match(missing.error, /Confirm updating/);
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

test('the controller never repeats a Poshmark listing after a completed or interrupted one', async () => {
  for (const fail of [false, true]) {
    const s = setup();
    Object.assign(s.msg.payload, { marketplace: 'poshmark', action: 'list', confirm: true, draft_url: null, photos: [{ id: 'a1' }] });
    let calls = 0;
    s.context.runPoshmarkApi = async () => {
      calls++;
      return fail ? { ok: false, safe_to_retry: false, error: 'Interrupted' }
        : { ok: true, state: 'published', listing_url: 'https://poshmark.com/listing/6adc371572e62463d992ffef' };
    };
    await s.context.handleNativeMarketplaceMessage(s.msg);
    assert.equal(s.replies[0].state, fail ? undefined : 'published');
    if (fail) assert.equal(s.replies[0].safe_to_retry, false);
    await s.context.handleNativeMarketplaceMessage(s.msg);
    assert.equal(calls, 1);
  }
});

test('a cancel aborts the request in flight', async () => {
  const s = setup();
  vm.runInContext("activeNativeOperation = { id: 'op-9', tabId: null, cancelled: false, controller: new AbortController() }", s.context);
  let signal;
  s.context.fetch = (_url, options) => new Promise((_resolve, reject) => {
    signal = options.signal;
    options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
  });
  const pending = s.context.marketplaceFetch('https://webapi.depop.com/presentation/api/v1/drafts/', { marketplace: 'depop', step: 'create',
    method: 'POST', body: '{}', deadline: Date.now() + 60000 });
  await new Promise(resolve => setImmediate(resolve));
  await s.context.cancelNativeMarketplaceOperation('op-9');
  const error = await pending.catch(value => value);
  assert.equal(signal.aborted, true);
  assert.equal(error.code, 'uncertain');
});
