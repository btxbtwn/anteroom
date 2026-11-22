const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker } = require('./worker-context');

// Response shapes follow what each marketplace returned for live and sold
// listings on 2026-10-25. Values are made up.
function worker(routes) {
  const c = loadWorker();
  Object.assign(c, { AbortSignal });
  c.chrome.declarativeNetRequest.updateSessionRules = async () => {};
  c.chrome.cookies = { get: async ({ name }) => (name === 'access_token_web'
    ? { value: `x.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.y` } : null) };
  const calls = [];
  c.fetch = async url => {
    calls.push(String(url));
    const route = Object.entries(routes).find(([part]) => String(url).includes(part));
    if (!route) return { ok: false, status: 404 };
    const [status, body] = route[1];
    return { ok: status === 200, status, json: async () => body, text: async () => body };
  };
  return { c, calls };
}

const read = async (c, marketplace, ids) => JSON.parse(JSON.stringify(await c.readMarketplaceSales(marketplace, ids)));

function mercariPage(id) {
  const data = { props: { pageProps: {
    req: { accessToken: 'page-token' },
    serverState: { [`ItemDetail:${id}`]: { seller: { __ref: 'PublicUser:42' }, name: 'Jacket', price: 12 } },
  } } };
  return `<html><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(data)}</script></html>`;
}

test('Poshmark reports a known listing missing from the live closet when that listing is sold out', async () => {
  const sold = 'aaaaaaaaaaaaaaaaaaaaaaaa';
  const live = 'bbbbbbbbbbbbbbbbbbbbbbbb';
  const seller = 'cccccccccccccccccccccccc';
  const { c, calls } = worker({
    [`/vm-rest/posts/${sold}`]: [200, { data: { id: sold, creator_id: seller, inventory: { status: 'sold_out' } } }],
    [`/vm-rest/posts/${live}`]: [200, { data: { id: live, creator_id: seller, inventory: { status: 'available' } } }],
    '/posts/filtered': [200, { data: [{ id: live }, { id: 'dddddddddddddddddddddddd' }], more: { next_max_id: null } }],
  });
  const gone = 'eeeeeeeeeeeeeeeeeeeeeeee';
  const result = await read(c, 'poshmark', [sold, live, gone, 'not-an-id']);
  assert.deepEqual(result, { ok: true, sales: [{ listing_id: sold }] });
  assert.equal(JSON.stringify(result).includes(seller), false);
  assert.equal(calls.filter(url => url.includes('/posts/filtered')).length, 1);
  assert.equal(calls.some(url => url.includes(`/vm-rest/posts/${live}`)), false);
});

test('Canadian Poshmark sale checks use the Canadian closet and listing endpoints', async () => {
  const sold = 'a'.repeat(24);
  const seller = 'c'.repeat(24);
  const { c, calls } = worker({
    [`poshmark.ca/vm-rest/posts/${sold}`]: [200, { data: { creator_id: seller, inventory: { status: 'sold_out' } } }],
    'poshmark.ca/vm-rest/users/': [200, { data: [] }],
  });
  const result = await c.readMarketplaceSales('poshmark', [sold], 'CA');
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, sales: [{ listing_id: sold }] });
  assert.equal(calls.length, 3);
  assert.ok(calls.every(url => new URL(url).host === 'poshmark.ca'));
});

test('Poshmark stops once every known listing is still in the closet', async () => {
  const live = 'bbbbbbbbbbbbbbbbbbbbbbbb';
  const seller = 'cccccccccccccccccccccccc';
  const { c, calls } = worker({
    [`/vm-rest/posts/${live}`]: [200, { data: { id: live, creator_id: seller, inventory: { status: 'available' } } }],
    '/posts/filtered': [200, { data: [{ id: live }], more: { next_max_id: 'next-page' } }],
  });
  assert.deepEqual(await read(c, 'poshmark', [live]), { ok: true, sales: [] });
  assert.equal(calls.filter(url => url.includes('/posts/filtered')).length, 1);
});

test('Mercari reads the seller trading list from the item page token', async () => {
  const { c, calls } = worker({
    '/us/item/m11111111111/': [200, mercariPage('m11111111111')],
    '/v1/api': [200, { data: { search: { itemsList: [
      { id: 'm11111111111', status: 'trading', name: 'Jacket', price: 12 },
      { id: 'm22222222222', status: 'on_sale' },
      { id: 'not-an-id', status: 'trading' },
    ] } } }],
  });
  const result = await read(c, 'mercari', ['m11111111111', 'm33333333333']);
  assert.deepEqual(result, { ok: true, sales: [{ listing_id: 'm11111111111' }] });
  assert.equal(JSON.stringify(result).includes('Jacket'), false);
  const search = calls.find(url => url.includes('/v1/api'));
  assert.equal(search, 'https://www.mercari.com/v1/api');
});

test('Depop reads the shop sold list, found from one of the seller\'s own listings', async () => {
  const { c, calls } = worker({
    '/products/by-slug/shop-gone-136d/': [404, null],
    '/products/by-slug/shop-classic-tee-136d/': [200, { id: 957631830, user_id: 29809510, slug: 'shop-classic-tee-136d', status: 'STATUS_ONSALE' }],
    '/shops/29809510/products/by-status/sold/': [200, { page_info: { has_more: true }, objects: [
      { id: 1, slug: 'shop-denim-jacket-0605', status: 'STATUS_PURCHASED', pricing: { current_price: '20.00' } },
      { id: 2, slug: 'shop-vinyl-5b3e', status: 'STATUS_MARKED_AS_SOLD' },
      { id: 3, slug: 'shop-other-1c02', status: 'STATUS_ONSALE' },
    ] }],
  });
  const result = await read(c, 'depop', ['shop-gone-136d', 'shop-classic-tee-136d']);
  assert.deepEqual(result, { ok: true, sales: [{ listing_id: 'shop-denim-jacket-0605' }, { listing_id: 'shop-vinyl-5b3e' }] });
  assert.equal(JSON.stringify(result).includes('20.00'), false);
  assert.equal(calls.at(-1), 'https://webapi.depop.com/presentation/api/v1/shops/29809510/products/by-status/sold/?limit=24');
});

test('Depop fails the check when no listing names the shop', async () => {
  const { c } = worker({});
  assert.equal((await read(c, 'depop', ['shop-gone-136d'])).ok, false);
});

test('a removed listing is not a sale, and a check that cannot read the shop fails', async () => {
  worker({
    '/api/listings/105101406': [200, { success: true, listing: { id: '105101406', status: 1, user: { id: '99' } } }],
    '/api/search/listings': [200, { success: true, listings: [{ id: '105101406' }], hasMore: false }],
  });
});

test('a sale check waits while another marketplace action is running', async () => {
  const { c } = worker({});
  c.fetch = async () => { throw new Error('should not read'); };
  vm.runInContext('claimNativeOperation("*", "busy")', c);
  assert.deepEqual(await read(c, 'poshmark', ['aaaaaaaaaaaaaaaaaaaaaaaa']), { ok: true, sales: [], skipped: true });
  vm.runInContext('nativeOperations.clear()', c);
});

test('Poshmark sales classifies HTTP and network failures and prepares request context', async () => {
  const post = 'aaaaaaaaaaaaaaaaaaaaaaaa';
  const seller = 'cccccccccccccccccccccccc';
  let rulesUpdated = 0;
  const ctx = worker({
    [`/vm-rest/posts/${post}`]: [200, { data: { id: post, creator_id: seller, inventory: { status: 'available' } } }],
    '/posts/filtered': [200, { data: [{ id: post }], more: { next_max_id: null } }],
  });
  ctx.c.chrome.declarativeNetRequest.updateSessionRules = async () => { rulesUpdated += 1; };
  assert.deepEqual(await read(ctx.c, 'poshmark', [post]), { ok: true, sales: [] });
  assert.equal(rulesUpdated, 1);

  for (const [status, code] of [[401, 'signed_out'], [403, 'signed_out'], [429, 'rate_limited'], [503, 'network']]) {
    const { c } = worker({ [`/vm-rest/posts/${post}`]: [status, {}] });
    const result = await read(c, 'poshmark', [post]);
    assert.equal(result.ok, false);
    assert.equal(result.error_code, code, `post HTTP ${status}`);
  }

  for (const [status, code] of [[401, 'signed_out'], [429, 'rate_limited'], [502, 'network']]) {
    const { c } = worker({
      [`/vm-rest/posts/${post}`]: [200, { data: { id: post, creator_id: seller, inventory: { status: 'available' } } }],
      '/posts/filtered': [status, {}],
    });
    const result = await read(c, 'poshmark', [post]);
    assert.equal(result.ok, false);
    assert.equal(result.error_code, code, `closet HTTP ${status}`);
  }

  const offline = worker({});
  offline.c.fetch = async () => { throw new TypeError('network down'); };
  const netResult = await read(offline.c, 'poshmark', [post]);
  assert.equal(netResult.ok, false);
  assert.equal(netResult.error_code, 'network');
});

test('Poshmark sales propagates signed_out during sale confirmation and avoids redundant sellerFrom retries', async () => {
  const live = 'bbbbbbbbbbbbbbbbbbbbbbbb';
  const missing = 'aaaaaaaaaaaaaaaaaaaaaaaa';
  const seller = 'cccccccccccccccccccccccc';
  // Seller lookup and closet loading succeed; per-item confirmation returns 401.
  const confirm401 = worker({
    [`/vm-rest/posts/${live}`]: [200, { data: { id: live, creator_id: seller, inventory: { status: 'available' } } }],
    '/posts/filtered': [200, { data: [{ id: live }], more: { next_max_id: null } }],
    [`/vm-rest/posts/${missing}`]: [401, {}],
  });
  const confirmResult = await read(confirm401.c, 'poshmark', [live, missing]);
  assert.equal(confirmResult.ok, false);
  assert.equal(confirmResult.error_code, 'signed_out');

  // 404 during confirmation stays a non-sale rather than failing the whole check.
  const confirm404 = worker({
    [`/vm-rest/posts/${live}`]: [200, { data: { id: live, creator_id: seller, inventory: { status: 'available' } } }],
    '/posts/filtered': [200, { data: [{ id: live }], more: { next_max_id: null } }],
    [`/vm-rest/posts/${missing}`]: [404, null],
  });
  assert.deepEqual(await read(confirm404.c, 'poshmark', [live, missing]), { ok: true, sales: [] });

  // sellerFrom stops immediately on signed_out instead of retrying additional IDs.
  const ids = [missing, live, 'dddddddddddddddddddddddd'];
  const fastFail = worker({
    [`/vm-rest/posts/${missing}`]: [401, {}],
    [`/vm-rest/posts/${live}`]: [200, { data: { id: live, creator_id: seller, inventory: { status: 'available' } } }],
  });
  const fastResult = await read(fastFail.c, 'poshmark', ids);
  assert.equal(fastResult.ok, false);
  assert.equal(fastResult.error_code, 'signed_out');
  assert.equal(fastFail.calls.length, 1);

  // 403 browser-check challenge pages classify as challenge (not signed_out) and stop immediately.
  const challenged = worker({
    [`/vm-rest/posts/${missing}`]: [403, '<html><title>Just a moment...</title><div class="cf-chl-widget"></div></html>'],
    [`/vm-rest/posts/${live}`]: [200, { data: { id: live, creator_id: seller, inventory: { status: 'available' } } }],
  });
  const challengeResult = await read(challenged.c, 'poshmark', ids);
  assert.equal(challengeResult.ok, false);
  assert.equal(challengeResult.error_code, 'challenge');
  assert.equal(challenged.calls.length, 1);
});
