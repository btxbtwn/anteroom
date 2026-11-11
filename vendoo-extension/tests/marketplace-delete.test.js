const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

const USER = 'a'.repeat(24);
const POST = 'b'.repeat(24);
const jwt = `x.${Buffer.from(JSON.stringify({ user_id: USER })).toString('base64')}.y`;

function worker(routes) {
  const c = loadWorker();
  c.AbortSignal = AbortSignal;
  c.setTimeout = (fn) => { fn(); return 0; };
  c.chrome.cookies = { get: async ({ name }) => ({ jwt: { value: jwt }, access_token: { value: 'private-token' } })[name] };
  c.chrome.declarativeNetRequest = { updateSessionRules: async () => {} };
  c.atob = atob;
  c.chrome.storage = { local: { get: async () => ({}), set: async () => {} } };
  c.chrome.tabs = { create: () => { throw new Error('opened a tab'); } };
  const requests = [];
  const calls = [];
  const counts = {};
  const PAGE = `<html><script>window.__INITIAL_STATE__={"csrftoken":"page-csrf","userCookies":{"userInfo":{"uid":"${USER}"}}};</script></html>`;
  routes = { 'GET poshmark.com/create-listing': [200, PAGE, { 'content-type': 'text/html' }],
    'GET www.mercari.com/v1/initialize': [200, { csrf: 'csrf', accessToken: 'token', user: { userId: 1, name: 'seller' } }], ...routes };
  c.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    const key = `${options.method || 'GET'} ${parsed.hostname}${parsed.pathname}`;
    requests.push(key);
    calls.push({ key, credentials: options.credentials, headers: options.headers || {} });
    counts[key] = (counts[key] || 0) + 1;
    const answer = routes[key];
    const [status, body, headers] = typeof answer === 'function' ? answer(counts[key], options) : answer || [404, {}];
    return reply(status, body, headers);
  };
  return { c, requests, calls };
}

const posh = `poshmark.com/vm-rest/posts/${POST}`;
const livePost = { data: { creator_id: USER, status: 'published', inventory: { status: 'available', size_quantities: [{ quantity_sold: 0 }] } } };

test('Poshmark delete reads, deletes once, and confirms it is gone', async () => {
  const { c, requests } = worker({
    [`GET ${posh}`]: n => [200, n === 1 ? livePost : { error: { errorType: 'PostRemovedError' } }],
    [`DELETE ${posh}`]: [200, {}],
  });
  assert.deepEqual({ ...(await c.runMarketplaceDelete({ marketplace: 'poshmark', listing_id: POST, confirm: true })) }, { ok: true, state: 'deleted' });
  assert.deepEqual(requests, ['GET poshmark.com/create-listing', `GET ${posh}`, `DELETE ${posh}`, `GET ${posh}`]);
});

test('Poshmark never deletes a sold or foreign listing, and a gone one is already deleted', async () => {
  const sold = { data: { ...livePost.data, inventory: { status: 'available', size_quantities: [{ quantity_sold: 1 }] } } };
  for (const body of [sold, { data: { ...livePost.data, creator_id: 'c'.repeat(24) } }]) {
    const { c, requests } = worker({ [`GET ${posh}`]: [200, body] });
    assert.equal((await c.runMarketplaceDelete({ marketplace: 'poshmark', listing_id: POST, confirm: true })).ok, false);
    assert.ok(!requests.some(r => r.startsWith('DELETE')));
  }
  const { c, requests } = worker({ [`GET ${posh}`]: [200, { error: { errorType: 'NotFoundError' } }] });
  assert.equal((await c.runMarketplaceDelete({ marketplace: 'poshmark', listing_id: POST, confirm: true })).already, true);
  assert.ok(!requests.some(r => r.startsWith('DELETE')));
});

const MERCARI_ID = 'm70565754838';
// The signed-in seller is userId 1 (the initialize stub above).
const mercariItem = (item = {}) => ({ data: { item: { itemId: MERCARI_ID, status: 'on_sale', seller: { id: 1 }, ...item } } });
function mercari(item, statusReply = { status: 'cancel' }) {
  const operations = [];
  const bodies = [];
  const { c } = worker({ 'POST www.mercari.com/v1/api': (n, options) => {
    const body = JSON.parse(options.body);
    operations.push(body.operationName);
    bodies.push(body);
    return [200, body.operationName === 'sellFetchItemsDetails' ? item : { data: { updateItemStatus: statusReply } }];
  } });
  return { c, operations, bodies, run: () => c.runMarketplaceDelete({ marketplace: 'mercari', listing_id: MERCARI_ID, confirm: true }) };
}

test('Mercari delete reads the item, then sends its own Delete status and checks the reply', async () => {
  const s = mercari(mercariItem());
  assert.equal((await s.run()).state, 'deleted');
  assert.deepEqual(s.operations, ['sellFetchItemsDetails', 'UpdateItemStatusMutation']);
  assert.deepEqual(s.bodies[1].variables, { input: { id: MERCARI_ID, status: 'cancel' } });
  assert.equal((await mercari(mercariItem(), { status: 'OK' }).run()).state, 'deleted');
  const refused = await mercari(mercariItem(), { status: 'trading' }).run();
  assert.equal(refused.ok, false);
  assert.equal(refused.marketplace_message, 'status trading');
});

test('Mercari never deletes a sold or foreign listing, and a cancelled one is already deleted', async () => {
  for (const item of [mercariItem({ seller: { id: 2 } }), mercariItem({ status: 'trading' }), mercariItem({ status: 'sold_out' })]) {
    const s = mercari(item);
    const result = await s.run();
    assert.equal(result.ok, false, JSON.stringify(item));
    assert.ok(!s.operations.includes('UpdateItemStatusMutation'), JSON.stringify(item));
  }
  for (const status of ['cancel', 'admin_cancel']) {
    const s = mercari(mercariItem({ status }));
    assert.equal((await s.run()).already, true, status);
    assert.deepEqual(s.operations, ['sellFetchItemsDetails']);
  }
});

test('a refused delete carries the marketplace\'s own words and is safe to repeat', async () => {
  const { c } = worker({ [`GET ${posh}`]: [200, livePost], [`DELETE ${posh}`]: [200, { error: { errorType: 'ProductReservedError', userMessage: 'This listing has an order in progress' } }] });
  const result = await c.runMarketplaceDelete({ marketplace: 'poshmark', listing_id: POST, confirm: true });
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.error_code, 'rejected');
  assert.equal(result.marketplace_message, 'This listing has an order in progress');
  const depop = worker({ 'GET webapi.depop.com/presentation/api/v1/products/by-slug/shop-tee-136d/': [429, { message: 'Too many requests' }, { 'retry-after': '30' }] });
  const limited = await depop.c.runMarketplaceDelete({ marketplace: 'depop', listing_id: 'shop-tee-136d', confirm: true });
  assert.equal(limited.error_code, 'rate_limited');
  assert.equal(limited.retry_after_s, 30);
  assert.equal(limited.marketplace_message, 'Too many requests');
});

test('Depop delete resolves the slug, refuses sold items, and confirms', async () => {
  const slug = 'webapi.depop.com/presentation/api/v1/products/by-slug/shop-tee-136d/';
  const { c, requests } = worker({
    [`GET ${slug}`]: n => (n === 1 ? [200, { id: 123456789, status: 'ONSALE' }] : [404, {}]),
    'DELETE webapi.depop.com/presentation/api/v1/products/123456789/': [204, {}],
  });
  assert.equal((await c.runMarketplaceDelete({ marketplace: 'depop', listing_id: 'shop-tee-136d', confirm: true })).state, 'deleted');
  assert.deepEqual(requests, [`GET ${slug}`, 'DELETE webapi.depop.com/presentation/api/v1/products/123456789/', `GET ${slug}`]);
  const sold = worker({ [`GET ${slug}`]: [200, { id: 123456789, status: 'PURCHASED' }] });
  assert.equal((await sold.c.runMarketplaceDelete({ marketplace: 'depop', listing_id: 'shop-tee-136d', confirm: true })).ok, false);
  assert.ok(!sold.requests.some(r => r.startsWith('DELETE')));
  const lagging = worker({
    [`GET ${slug}`]: n => [n < 3 ? 200 : 404, { id: 123456789, status: 'ONSALE' }],
    'DELETE webapi.depop.com/presentation/api/v1/products/123456789/': [204, {}],
  });
  assert.equal((await lagging.c.runMarketplaceDelete({ marketplace: 'depop', listing_id: 'shop-tee-136d', confirm: true })).state, 'deleted');
  assert.equal(lagging.requests.filter(request => request.startsWith('GET')).length, 3);
});

test('nothing is deleted without confirmation or a valid ID', async () => {
  const { c, requests } = worker({});
  for (const payload of [{ marketplace: 'poshmark', listing_id: POST }, { marketplace: 'mercari', listing_id: '../x', confirm: true },
    { marketplace: 'ebay', listing_id: '123456789012', confirm: true }]) {
    assert.equal((await c.runMarketplaceDelete(payload)).ok, false);
  }
  assert.equal(requests.length, 0);
});
