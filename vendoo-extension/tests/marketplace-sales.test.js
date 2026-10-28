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
  // Vinted's hour-long access token, with time left on it.
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

test('Sellwild reports a known listing missing from the live shop when its status is 5', async () => {
  const { c, calls } = worker({
    '/api/listings/105101406': [200, { success: true, listing: { id: '105101406', status: 5, user: { id: '99', firstName: 'Pat' } } }],
    '/api/listings/105232291': [200, { success: true, listing: { id: '105232291', status: 1, user: { id: '99' } } }],
    '/api/search/listings': [200, { success: true, listings: [{ id: 105232291 }, { id: 105000001 }], hasMore: false }],
  });
  const result = await read(c, 'sellwild', ['105101406', '105232291', '105999999']);
  assert.deepEqual(result, { ok: true, sales: [{ listing_id: '105101406' }] });
  assert.equal(JSON.stringify(result).includes('Pat'), false);
  assert.equal(calls.some(url => url.includes('/api/search/listings?userId=99&limit=200&page=1')), true);
  assert.equal(calls.some(url => url.includes('/api/listings/105232291')), false);
});

test('a removed listing is not a sale, and a check that cannot read the shop fails', async () => {
  const { c } = worker({
    '/api/listings/105101406': [200, { success: true, listing: { id: '105101406', status: 1, user: { id: '99' } } }],
    '/api/search/listings': [200, { success: true, listings: [{ id: '105101406' }], hasMore: false }],
  });
  assert.deepEqual(await read(c, 'sellwild', ['105101406', '105232291']), { ok: true, sales: [] });
  assert.equal((await read(c, 'sellwild', ['105232291'])).ok, false);
});

test('Vinted reports a known listing missing from the live wardrobe when that listing is closed', async () => {
  const { c, calls } = worker({
    '/api/v2/users/current': [200, { user: { id: 7, login: 'seller', email: 'seller@example.com' } }],
    '/api/v2/wardrobe/7/items': [200, { items: [
      { id: 100, title: 'Live jacket', is_draft: false, is_closed: false, is_hidden: false },
      { id: 204, title: 'Draft jacket', is_draft: true, is_closed: false },
    ], pagination: { total_pages: 1 } }],
    '/api/v2/items/200': [200, { item: { id: 200, title: 'Sold jacket', price: 12, is_closed: true, is_hidden: false, is_reserved: false, is_draft: false } }],
    '/api/v2/items/202': [200, { item: { id: 202, is_closed: true, is_hidden: true, is_reserved: false, is_draft: false } }],
    '/api/v2/items/203': [200, { item: { id: 203, is_closed: true, is_hidden: false, is_reserved: true, is_draft: false } }],
    '/api/v2/items/204': [200, { item: { id: 204, is_closed: false, is_hidden: false, is_reserved: false, is_draft: true } }],
  });
  const result = await read(c, 'vinted', ['100', '200', '201', '202', '203', '204']);
  assert.deepEqual(result, { ok: true, sales: [{ listing_id: '200' }] });
  assert.equal(JSON.stringify(result).includes('Sold jacket'), false);
  assert.equal(JSON.stringify(result).includes('seller@example.com'), false);
  assert.equal(calls.some(url => url.includes('/api/v2/wardrobe/7/items?page=1&per_page=96&order=newest_first')), true);
  assert.equal(calls.some(url => url.includes('/api/v2/items/100')), false);
});

test('a sale check waits while another marketplace action is running', async () => {
  const { c } = worker({});
  c.fetch = async () => { throw new Error('should not read'); };
  vm.runInContext('claimNativeOperation("*", "busy")', c);
  assert.deepEqual(await read(c, 'poshmark', ['aaaaaaaaaaaaaaaaaaaaaaaa']), { ok: true, sales: [], skipped: true });
  vm.runInContext('nativeOperations.clear()', c);
});
