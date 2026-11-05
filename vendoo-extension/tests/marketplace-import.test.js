const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

// Response shapes follow each marketplace's own pages and the readers already in
// the extension. Values are made up. No live marketplace is called.
const POSH_ME = 'aaaaaaaaaaaaaaaaaaaaaaaa';
const POSH_POST = 'bbbbbbbbbbbbbbbbbbbbbbbb';
const jwt = claims => `x.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.y`;

function worker(routes, { cookies = {} } = {}) {
  const c = loadWorker();
  c.chrome.cookies = { get: async ({ name }) => (cookies[name] ? { value: cookies[name], session: true } : null) };
  c.chrome.tabs = { create: () => { throw new Error('opened a tab'); }, query: () => { throw new Error('read tabs'); } };
  const requests = [];
  c.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    const method = options.method || 'GET';
    requests.push({ method, url: String(url), pathname: parsed.pathname, body: options.body, credentials: options.credentials, headers: options.headers });
    for (const [key, handler] of Object.entries(routes)) {
      if (`${parsed.host}${parsed.pathname}` === key || parsed.pathname === key) {
        const value = typeof handler === 'function' ? handler(parsed, options) : handler;
        return reply(...value);
      }
    }
    return reply(404, { message: 'missing' });
  };
  return { c, requests };
}

const plain = value => JSON.parse(JSON.stringify(value));
const page = async (c, marketplace, cursor = null) => plain(await c.readMarketplaceImportPage(marketplace, cursor));
const listing = async (c, marketplace, id) => plain(await c.readMarketplaceImportListing(marketplace, id));

function readOnly(requests) {
  for (const request of requests) {
    if (request.method === 'GET') continue;
    // Mercari answers its queries over POST; none of them may be a mutation.
    assert.equal(request.url, 'https://www.mercari.com/v1/api');
    const body = JSON.parse(request.body);
    assert.match(body.operationName, /Query$|^sellFetchItemsDetails$/);
    assert.equal(/mutation/i.test(body.query || ''), false);
  }
}

const POSH_CATALOG = { data: { catalog: { departments: [{ id: 'd1', display: 'Women', categories: [
  { id: 'c1', display: 'Jackets & Coats', category_features: [{ id: 'f1', display: 'Jean Jackets' }] }] }] } } };
const POSH_FULL = {
  id: POSH_POST, creator_id: POSH_ME, title: 'Denim jacket', description: 'Classic fit.\nNo flaws.',
  price_amount: { val: '45', currency_code: 'USD' }, brand: 'Levi\'s', condition: 'not_nwt',
  catalog: { department: 'd1', category: 'c1', category_features: ['f1'] },
  inventory: { status: 'available', size_quantities: [{ size_id: 'M', size_obj: { display: 'M' }, quantity_available: 2,
    seller_inventory_private_info: { sku: 'JKT-1' } }] },
  cover_shot: { url_large: 'https://di2ponv0v5otw.cloudfront.net/posts/1.jpg' },
  pictures: [{ url_large: 'https://di2ponv0v5otw.cloudfront.net/posts/2.jpg' }, { url_large: 'http://insecure.example/3.jpg' }],
  colors: [{ name: 'Blue' }],
};

test('Poshmark reads the signed-in closet a page at a time and only with GETs', async () => {
  let request;
  const { c, requests } = worker({
    [`/vm-rest/users/${POSH_ME}/posts/filtered`]: parsed => {
      request = JSON.parse(parsed.searchParams.get('request'));
      return [200, { data: [{ id: POSH_POST, title: 'Denim jacket', price_amount: { val: '45', currency_code: 'USD' },
        inventory: { size_quantities: [{ quantity_available: 2 }] } }, { id: 'not-a-post' }], more: { next_max_id: 'next-1' } }];
    },
  }, { cookies: { jwt: jwt({ user_id: POSH_ME }) } });
  const result = await page(c, 'poshmark', 'prev-0');
  assert.deepEqual(result, { ok: true, next_cursor: 'next-1', rows: [
    { id: POSH_POST, url: `https://poshmark.com/listing/${POSH_POST}`, title: 'Denim jacket', price: 45, quantity: 2 }] });
  assert.deepEqual(request.filters.inventory_status, ['available']);
  assert.equal(request.max_id, 'prev-0');
  readOnly(requests);
});

test('Poshmark returns a full listing with category names and https photos', async () => {
  const { c, requests } = worker({
    [`/vm-rest/posts/${POSH_POST}`]: [200, { data: POSH_FULL }],
    '/vm-rest/meta/catalog_v2': [200, POSH_CATALOG],
  }, { cookies: { jwt: jwt({ user_id: POSH_ME }) } });
  const result = await listing(c, 'poshmark', POSH_POST.toUpperCase());
  assert.equal(result.ok, true);
  assert.deepEqual(result.listing, {
    id: POSH_POST, url: `https://poshmark.com/listing/${POSH_POST}`, title: 'Denim jacket', description: 'Classic fit.\nNo flaws.',
    price: 45, currency: 'USD', quantity: 2, brand: 'Levi\'s', size: 'M', condition: 'not_nwt',
    category_path: 'Women > Jackets & Coats > Jean Jackets', category_id: 'd1/c1/f1', sku: 'JKT-1', colors: ['Blue'],
    photos: ['https://di2ponv0v5otw.cloudfront.net/posts/1.jpg', 'https://di2ponv0v5otw.cloudfront.net/posts/2.jpg'],
  });
  readOnly(requests);
});

test('Poshmark refuses another closet, sold listings and several sizes', async () => {
  const run = async post => {
    const { c } = worker({ [`/vm-rest/posts/${POSH_POST}`]: [200, { data: { ...POSH_FULL, ...post } }], '/vm-rest/meta/catalog_v2': [200, POSH_CATALOG] },
      { cookies: { jwt: jwt({ user_id: POSH_ME }) } });
    return listing(c, 'poshmark', POSH_POST);
  };
  assert.match((await run({ creator_id: 'cccccccccccccccccccccccc' })).error, /does not belong/);
  assert.match((await run({ inventory: { status: 'sold_out' } })).error, /not available/);
  assert.match((await run({ inventory: { status: 'available', size_quantities: [{ size_id: 'S' }, { size_id: 'M' }] } })).error, /several sizes/);
  const { c } = worker({}, {});
  assert.equal((await listing(c, 'poshmark', POSH_POST)).error_code, 'signed_out');
});

const MERCARI_INIT = [200, { csrf: 'csrf', accessToken: 'token', user: { userId: 42, name: 'seller' } }];
const MERCARI_MASTER = { master: {
  itemCategories: [{ id: 1, name: 'Women', parentId: 0 }, { id: 11, name: 'Coats & jackets', parentId: 1 }, { id: 111, name: 'Jean jackets', parentId: 11 }],
  itemSizeGroups: [], itemSizeSubGroups: [], itemSizes: [{ id: 5, title: 'M' }], itemBrands: [{ id: 9, name: 'Levi\'s' }],
} };

function mercari(item, search = []) {
  const ops = [];
  const { c, requests } = worker({
    'www.mercari.com/v1/initialize': MERCARI_INIT,
    'www.mercari.com/v1/api': (_parsed, options) => {
      const body = JSON.parse(options.body);
      ops.push(body);
      if (body.operationName === 'masterQuery') return [200, { data: MERCARI_MASTER }];
      if (body.operationName === 'publicProfileSearchQuery') return [200, { data: { search: { itemsList: search } } }];
      if (body.operationName === 'sellFetchItemsDetails') return [200, { data: { item } }];
      return [200, { errors: [{ message: 'unknown' }] }];
    },
  });
  return { c, requests, ops };
}

const MERCARI_ITEM = { itemId: 'm12345678901', name: 'Denim jacket', description: 'Classic fit', status: 'on_sale', price: 4500,
  photos: [{ imageUrl: 'https://u-mercari-images.mercdn.net/photos/m1_1.jpg' }], itemCondition: { id: 3 }, itemSize: { id: 5 },
  brand: { id: 9 }, itemCategory: { id: 111 }, seller: { id: 42 } };

test('Mercari lists only on-sale items for the signed-in seller', async () => {
  const items = Array.from({ length: 48 }, (_, index) => ({ id: `m1000000${String(index).padStart(4, '0')}`, name: `Item ${index}`, status: 'on_sale', price: 1200 }));
  items[1] = { ...items[1], status: 'trading' };
  const { c, requests, ops } = mercari(MERCARI_ITEM, items);
  const result = await page(c, 'mercari', '48');
  assert.equal(result.ok, true);
  assert.equal(result.rows.length, 47);
  assert.deepEqual(result.rows[0], { id: 'm10000000000', url: 'https://www.mercari.com/us/item/m10000000000/', title: 'Item 0', price: 12, quantity: 1 });
  assert.equal(result.next_cursor, '96');
  const search = ops.find(op => op.operationName === 'publicProfileSearchQuery');
  assert.deepEqual(search.variables.criteria, { itemStatuses: [1], sellerIds: [42], length: 48, offset: 48 });
  readOnly(requests);
});

test('Mercari reads its edit-page item details and names the catalog ids', async () => {
  const { c, requests, ops } = mercari(MERCARI_ITEM);
  const result = await listing(c, 'mercari', 'm12345678901');
  assert.deepEqual(result.listing, {
    id: 'm12345678901', url: 'https://www.mercari.com/us/item/m12345678901/', title: 'Denim jacket', description: 'Classic fit',
    price: 45, currency: 'USD', quantity: 1, brand: 'Levi\'s', size: 'M', condition: 'Good',
    category_path: 'Women > Coats & jackets > Jean jackets', category_id: '111', sku: null, colors: [],
    photos: ['https://u-mercari-images.mercdn.net/photos/m1_1.jpg'],
  });
  assert.deepEqual(ops.find(op => op.operationName === 'sellFetchItemsDetails').variables, { itemId: 'm12345678901' });
  readOnly(requests);
});

test('Mercari refuses another seller and an item that is not on sale', async () => {
  assert.match((await listing(mercari({ ...MERCARI_ITEM, seller: { id: 7 } }).c, 'mercari', 'm12345678901')).error, /does not belong/);
  assert.match((await listing(mercari({ ...MERCARI_ITEM, status: 'trading' }).c, 'mercari', 'm12345678901')).error, /not on sale/);
});

const DEPOP_ATTRIBUTES = { department: [{ id: 'womenswear', name: 'Womenswear' }],
  group: [{ id: 'outerwear', name: 'Coats and jackets', product_types: [{ id: 'denim-jackets', name: 'Denim jackets' }] }],
  brand: [{ id: 77, name: 'Levi\'s' }], generic_attributes: [] };
const DEPOP_SIZES = { category_size_mapping: [], size_sets: [{ id: 3, sizes: [{ id: 4, name: 'M' }] }] };
const DEPOP_PRODUCT = { id: 957, slug: 'shop-denim-jacket-136d', user_id: 5, status: 'STATUS_ONSALE', description: 'Denim jacket #levis\nClassic fit',
  price_amount: '45.00', price_currency: 'USD', brand_id: 77, condition: 'used_excellent', gender: 'womenswear', product_type: 'denim-jackets',
  variant_set: 3, variants: { 4: 1 }, sku: 'JKT-1', colour: ['blue'],
  pictures: [{ id: 1, formats: { P0: { url: 'https://media-photos.depop.com/b1/1/P0.jpg' } } }] };

function depop(product = DEPOP_PRODUCT, shop = {}) {
  return worker({
    '/presentation/api/v1/users/me/': [200, { id: 5 }],
    '/api/v1/shop/5/products/': [200, shop],
    [`/presentation/api/v1/products/by-slug/${product.slug}/`]: [200, product],
    '/presentation/api/v1/attributes/': [200, DEPOP_ATTRIBUTES],
    '/presentation/api/v1/attributes/categories/size-mapping/': [200, DEPOP_SIZES],
  }, { cookies: { access_token: 'secret-token' } });
}

test('Depop reads the shop inventory with the seller bearer token and skips sold products', async () => {
  const { c, requests } = depop(DEPOP_PRODUCT, { objects: [
    { slug: 'shop-denim-jacket-136d', status: 'STATUS_ONSALE', description: 'Denim jacket #levis', price_amount: '45.00' },
    { slug: 'shop-sold-tee-136d', status: 'STATUS_PURCHASED', description: 'Sold tee' },
  ], meta: { last_offset_id: 'cursor-2', end: false } });
  const result = await page(c, 'depop');
  assert.deepEqual(result, { ok: true, next_cursor: 'cursor-2', rows: [{ id: 'shop-denim-jacket-136d',
    url: 'https://www.depop.com/products/shop-denim-jacket-136d/', title: 'Denim jacket', price: 45, quantity: null }] });
  const shop = requests.find(request => request.pathname === '/api/v1/shop/5/products/');
  assert.equal(shop.credentials, 'omit');
  assert.equal(shop.headers.Authorization, 'Bearer secret-token');
  readOnly(requests);
});

test('Depop returns the product with names from its attribute list', async () => {
  const { c, requests } = depop();
  const result = await listing(c, 'depop', 'shop-denim-jacket-136d');
  assert.deepEqual(result.listing, {
    id: 'shop-denim-jacket-136d', url: 'https://www.depop.com/products/shop-denim-jacket-136d/', title: 'Denim jacket',
    description: 'Denim jacket #levis\nClassic fit', price: 45, currency: 'USD', quantity: 1, brand: 'Levi\'s', size: 'M',
    condition: 'used_excellent', category_path: 'Womenswear > Coats and jackets > Denim jackets', category_id: 'womenswear_denim-jackets',
    sku: 'JKT-1', colors: ['blue'], photos: ['https://media-photos.depop.com/b1/1/P0.jpg'],
  });
  readOnly(requests);
});

test('Depop takes the price from the shop list when its product document has none', async () => {
  // Live shape (2026-10-30): by-slug has no price; the shop list carries price.price_amount.
  const bare = { ...DEPOP_PRODUCT, price_amount: undefined, price_currency: undefined };
  const shopRow = { slug: DEPOP_PRODUCT.slug, status: 'STATUS_ONSALE', description: 'Denim jacket',
    price: { price_amount: '45.00', currency_name: 'USD', discounted_price_amount: '33.75' } };
  const found = depop(bare, { products: [{ slug: 'other-tee-136d', price: { price_amount: '9.00', currency_name: 'USD' } }, shopRow],
    meta: { end: true } });
  const result = await listing(found.c, 'depop', DEPOP_PRODUCT.slug);
  assert.equal(result.listing.price, 45);
  readOnly(found.requests);
  const gbp = depop(bare, { products: [{ ...shopRow, price: { price_amount: '45.00', currency_name: 'GBP' } }], meta: { end: true } });
  assert.match((await listing(gbp.c, 'depop', DEPOP_PRODUCT.slug)).error, /USD/);
  const missing = depop(bare, { products: [], meta: { end: true } });
  assert.equal((await listing(missing.c, 'depop', DEPOP_PRODUCT.slug)).listing.price, null);
});

test('Depop refuses another shop, several sizes and other currencies', async () => {
  assert.match((await listing(depop({ ...DEPOP_PRODUCT, user_id: 6 }).c, 'depop', DEPOP_PRODUCT.slug)).error, /does not belong/);
  assert.match((await listing(depop({ ...DEPOP_PRODUCT, variants: { 4: 1, 5: 1 } }).c, 'depop', DEPOP_PRODUCT.slug)).error, /several sizes/);
  assert.match((await listing(depop({ ...DEPOP_PRODUCT, price_currency: 'GBP' }).c, 'depop', DEPOP_PRODUCT.slug)).error, /USD/);
});

test('import reads wait for a running listing action and reject malformed IDs without a request', async () => {
  const { c, requests } = depop();
  assert.equal((await listing(c, 'depop', '../slug')).ok, false);
  assert.equal((await listing(c, 'ebay', '123')).ok, false);
  require('node:vm').runInContext('claimNativeOperation("*", "op")', c);
  assert.equal((await page(c, 'depop')).error_code, 'busy');
  assert.equal((await listing(c, 'depop', DEPOP_PRODUCT.slug)).error_code, 'busy');
  assert.equal(requests.length, 0);
});

// On the web the seller's Anteroom tab relays the same reads, and gets the same answer.
test('a relayed import reads the same closet, and a refusal carries only the Bridge words and code', async () => {
  const { c, requests } = worker({
    [`/vm-rest/users/${POSH_ME}/posts/filtered`]: [200, { data: [{ id: POSH_POST, title: 'Denim jacket',
      price_amount: { val: '45', currency_code: 'USD' }, inventory: { size_quantities: [{ quantity_available: 2 }] } }], more: {} }],
    [`/vm-rest/posts/${POSH_POST}`]: [200, { data: { ...POSH_FULL, creator_id: 'cccccccccccccccccccccccc' } }],
    '/vm-rest/meta/catalog_v2': [200, POSH_CATALOG],
  }, { cookies: { jwt: jwt({ user_id: POSH_ME }) } });
  const id = 'e'.repeat(32);
  const run = async (type, payload) => plain(await c.runCloudMarketplaceCommand({ type, job_id: null, message_id: id, payload: { request_id: id, ...payload } }, []));
  assert.deepEqual(await run('marketplace.import.page', { marketplace: 'poshmark' }), await page(c, 'poshmark'));
  const refused = await run('marketplace.import.listing', { marketplace: 'poshmark', listing_id: POSH_POST });
  assert.deepEqual(Object.keys(refused).sort(), ['error', 'error_code', 'ok']);
  assert.equal(refused.ok, false);
  assert.match(refused.error, /does not belong/);
  readOnly(requests);
});
