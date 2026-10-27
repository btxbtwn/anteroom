// Update of a live Mercari, Vinted or Sellwild listing: one seller-confirmed
// write onto the listing already up, keeping its photos, confirmed by reading
// it back. Each fake holds the live listing and changes it only on the edit.
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

const plain = value => JSON.parse(JSON.stringify(value));

function journaled(c) {
  const journal = {};
  c.chrome.storage = {
    local: { get: async key => ({ [key]: journal[key] }), set: async values => Object.assign(journal, values), remove: async () => {} },
  };
  c.chrome.declarativeNetRequest = { updateSessionRules: async () => {} };
  c.chrome.tabs = { create: () => { throw new Error('opened a tab'); }, query: () => { throw new Error('read tabs'); } };
  c.chrome.scripting = { executeScript: () => { throw new Error('ran a page script'); } };
  c.nativePhoto = async () => assert.fail('an update uploads no photos');
  return journal;
}

// ---- Mercari ----

const MERCARI_SESSION = { csrf: 'private-csrf', accessToken: 'private-session', user: { userId: 7, name: 'seller' } };
const MERCARI_PHOTOS = [{ imageUrl: 'https://u-mercari-images.mercdn.net/photos/m12345678_1.jpg' },
  { imageUrl: 'https://u-mercari-images.mercdn.net/photos/m12345678_2.jpg' }];
const mercariItem = () => ({
  itemId: 'm12345678', name: 'Test jacket', description: 'Private description', price: 2000, status: 'on_sale',
  photos: MERCARI_PHOTOS, itemCondition: { id: 3 }, itemCategory: { id: 303 }, brand: { id: 55 }, itemSize: { id: 9 },
  shippingPayer: { id: 1 }, shippingClasses: [{ id: 2509 }], shippingPackageWeight: { weight: 10, unit: 'OUNCE' },
  shippingPackageDimension: null, seller: { id: 7, zipCode: '70125' }, isAutoPriceDrop: false, minPriceForAutoPriceDrop: 0,
});
const mercariListing = { title: 'Test jacket', description: 'Private description', price: 25, condition: 'Pre-Owned - Good',
  marketplace_category_ids: { mercari: '303' }, zipCode: '70125', weight_lb: 0, weight_oz: 10,
  form: { specifics: { shipping: { payerId: 1, carrierId: '2509' } } } };

function mercari({ item = mercariItem(), keep = true, edit } = {}) {
  const c = loadWorker();
  journaled(c);
  c.mercariPreparedListing = async value => value;
  const requests = [];
  let live = item;
  c.fetch = async (url, options) => {
    if (url === 'https://www.mercari.com/v1/initialize') return reply(200, MERCARI_SESSION);
    const body = JSON.parse(options.body);
    requests.push(body);
    if (body.operationName === 'sellFetchItemsDetails') return reply(200, { data: { item: live } });
    if (body.operationName === 'editListing') {
      if (edit) return edit(body);
      const input = body.variables.input;
      if (keep) live = { ...live, name: input.name, description: input.description, price: input.price };
      return reply(200, { data: { editListing: { id: input.id, seller: { id: 7, numSellItems: 3 } } } });
    }
    return reply(200, { errors: [{ message: `unexpected ${body.operationName}` }] });
  };
  const ops = () => requests.map(body => body.operationName);
  return { c, requests, ops };
}

const mercariUpdate = extra => ({ job_id: 'job-1', payload: { marketplace: 'mercari', action: 'update', confirm: true,
  operation_id: 'op-1', listing_id: 'm12345678', listing: mercariListing, photos: [{ id: 'p1', mime_type: 'image/jpeg' }], ...extra } });

test('a Mercari update sends one editListing with the live photos and values, changing only the price, and reads it back', async () => {
  const { c, requests, ops } = mercari();
  const result = plain(await c.runMercariNative(mercariUpdate()));
  assert.deepEqual(result, { ok: true, state: 'published', listing_url: 'https://www.mercari.com/us/item/m12345678/' });
  assert.deepEqual(ops(), ['sellFetchItemsDetails', 'editListing', 'sellFetchItemsDetails']);
  const edit = requests[1];
  assert.equal(edit.extensions.persistedQuery.sha256Hash, '53785e33b5b6ddeaedd8a31b9cfdccc0b00cd6e571810e369c87e5c74ddf591d');
  assert.equal(edit.query, undefined);
  assert.deepEqual(edit.variables.input, {
    id: 'm12345678', photoIds: MERCARI_PHOTOS.map(photo => photo.imageUrl), name: 'Test jacket', description: 'Private description',
    price: 2500, categoryId: 303, conditionId: 3, brandId: 55, sizeId: 9, zipCode: '70125', shippingPayerId: 1,
    shippingClassIds: [2509], shippingPackageWeight: 10, shippingWeightUnit: 'OUNCE',
  });
});

test('a Mercari listing that already matches is not written', async () => {
  const { c, ops } = mercari({ item: { ...mercariItem(), price: 2500 } });
  const result = plain(await c.runMercariNative(mercariUpdate()));
  assert.equal(result.ok, true);
  assert.deepEqual(ops(), ['sellFetchItemsDetails']);
});

test('a Mercari update refuses another account, an item not on sale, or no confirmation, before writing', async () => {
  for (const [item, extra] of [[{ ...mercariItem(), seller: { id: 8 } }, {}], [{ ...mercariItem(), status: 'sold_out' }, {}], [mercariItem(), { confirm: false }]]) {
    const { c, ops } = mercari({ item });
    const result = plain(await c.runMercariNative(mercariUpdate(extra)));
    assert.equal(result.ok, false);
    assert.equal(result.safe_to_retry, true);
    assert.ok(!ops().includes('editListing'));
  }
});

test('Mercari refusing the edit is safe to retry; a read-back that disagrees is unknown and is not sent again', async () => {
  const refused = mercari({ edit: () => reply(200, { errors: [{ message: 'Price is too low' }] }) });
  const first = plain(await refused.c.runMercariNative(mercariUpdate()));
  assert.equal(first.safe_to_retry, true);
  assert.equal(first.marketplace_message, 'Price is too low');

  const lost = mercari({ keep: false });
  const second = plain(await lost.c.runMercariNative(mercariUpdate()));
  assert.equal(second.ok, false);
  assert.equal(second.error_code, 'uncertain');
  assert.equal(second.safe_to_retry, false);
  assert.match(second.error, /price/);
  assert.equal(lost.ops().filter(op => op === 'editListing').length, 1);
});

test('a lost Mercari update reply is answered by reading the item, never by editing again', async () => {
  const done = mercari({ item: { ...mercariItem(), price: 2500 } });
  assert.equal(plain(await done.c.runMercariNative(mercariUpdate({ confirm: undefined }), true)).ok, true);
  const missing = mercari();
  const result = plain(await missing.c.runMercariNative(mercariUpdate({ confirm: undefined }), true));
  assert.equal(result.error_code, 'uncertain');
  assert.equal(result.safe_to_retry, false);
  assert.ok(![...done.ops(), ...missing.ops()].includes('editListing'));
});

// ---- Vinted ----

const VINTED_PAGE = '<html><script>"CSRF_TOKEN":"csrf-secret"</script></html>';
const VINTED_USER = { user: { id: 7, login: 'seller', currency: 'USD' } };
const VINTED_CATALOGS = { catalogs: [{ id: 1, title: 'Women', catalogs: [{ id: 1904, title: 'Straight jeans' }] }] };
const VINTED_ATTRIBUTES = { attributes: [{ code: 'condition', configuration: { options: [{ id: 3, title: 'Good' }] } },
  { code: 'size', configuration: { options: [] } }] };
const vintedItem = () => ({
  id: 42, title: 'Test jeans', description: 'Private description', price: { amount: '20.0', currency_code: 'USD' },
  catalog_id: 1904, status_id: 3, brand_id: 88, brand: 'Levi’s', color_ids: [1], package_size_id: 1,
  photos: [{ id: 11, orientation: 0 }, { id: 12, orientation: 90 }],
  item_attributes: [{ code: 'condition', ids: [3] }, { code: 'material', ids: [44] }],
  is_draft: false, is_closed: false, is_hidden: false, is_reserved: false, user: { id: 7 },
});
const vintedListing = { title: 'Test jeans', description: 'Private description', price: 24,
  vinted_specifics: { category: 'Women > Straight jeans', condition: 'Good' } };

function vinted({ item = vintedItem(), keep = true } = {}) {
  const c = loadWorker();
  journaled(c);
  const requests = [];
  let live = item;
  c.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    const { pathname } = new URL(url);
    requests.push({ method, pathname, headers: options.headers, body: options.body });
    if (pathname === '/items/new') return reply(200, VINTED_PAGE);
    if (pathname === '/api/v2/users/current') return reply(200, VINTED_USER);
    if (pathname === '/api/v2/item_upload/catalogs') return reply(200, VINTED_CATALOGS);
    if (pathname === '/api/v2/item_upload/attributes') return reply(200, VINTED_ATTRIBUTES);
    if (pathname === '/api/v2/item_upload/size_groups') return reply(200, { size_groups: [] });
    if (pathname.endsWith('/package_sizes')) return reply(200, { package_sizes: [{ id: 2, title: 'Medium' }, { id: 1, title: 'Small' }] });
    if (method === 'PUT' && pathname === '/api/v2/item_upload/items/42') {
      const sent = JSON.parse(options.body).item;
      if (keep) live = { ...live, title: sent.title, description: sent.description, price: { amount: String(sent.price) } };
      return reply(200, { item: { id: 42 } });
    }
    if (method === 'GET' && (pathname === '/api/v2/item_upload/items/42' || pathname === '/api/v2/items/42')) return reply(200, { item: live });
    return reply(404, { message: 'missing' });
  };
  const sent = (method, path) => requests.filter(request => request.method === method && request.pathname === path);
  return { c, requests, sent };
}

const vintedUpdate = extra => ({ job_id: 'job-1', payload: { marketplace: 'vinted', action: 'update', confirm: true,
  operation_id: 'op-1', listing_id: '42', listing: vintedListing, photos: [{ id: 'p1', mime_type: 'image/jpeg' }], ...extra } });

test('a Vinted update saves the live item once with its own photos and kept values, and reads it back', async () => {
  const { c, sent } = vinted();
  const result = plain(await c.runVintedNative(vintedUpdate()));
  assert.deepEqual(result, { ok: true, state: 'published', listing_url: 'https://www.vinted.com/items/42' });
  assert.equal(sent('POST', '/api/v2/photos').length, 0);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
  const puts = sent('PUT', '/api/v2/item_upload/items/42');
  assert.equal(puts.length, 1);
  assert.equal(puts[0].headers['x-csrf-token'], 'csrf-secret');
  const body = JSON.parse(puts[0].body);
  assert.equal(body.push_up, false);
  assert.equal(body.parcel, null);
  assert.equal(body.upload_session_id, body.item.temp_uuid);
  const { temp_uuid: _session, ...item } = body.item;
  assert.deepEqual(item, {
    id: 42, currency: 'USD', title: 'Test jeans', description: 'Private description', brand_id: 88, brand: 'Levi’s', size_id: null,
    catalog_id: 1904, isbn: null, is_unisex: false, status_id: 3, price: 24, package_size_id: 1,
    shipment_prices: { domestic: null, international: null }, color_ids: [1],
    assigned_photos: [{ id: 11, orientation: 0 }, { id: 12, orientation: 90 }],
    item_attributes: [{ code: 'material', ids: [44] }, { code: 'condition', ids: [3] }],
    manufacturer: null, manufacturer_labelling: null,
  });
});

test('a Vinted listing that already matches is not written, and one that is not the seller’s or not live is refused', async () => {
  const same = vinted({ item: { ...vintedItem(), price: { amount: '24.0' } } });
  assert.equal(plain(await same.c.runVintedNative(vintedUpdate())).ok, true);
  assert.equal(same.sent('PUT', '/api/v2/item_upload/items/42').length, 0);
  for (const item of [{ ...vintedItem(), user: { id: 8 } }, { ...vintedItem(), is_closed: true }]) {
    const { c, sent } = vinted({ item });
    const result = plain(await c.runVintedNative(vintedUpdate()));
    assert.equal(result.ok, false);
    assert.equal(result.safe_to_retry, true);
    assert.equal(sent('PUT', '/api/v2/item_upload/items/42').length, 0);
  }
});

test('an unconfirmed Vinted update sends nothing; a read-back that disagrees is unknown', async () => {
  const unconfirmed = vinted();
  assert.equal(plain(await unconfirmed.c.runVintedNative(vintedUpdate({ confirm: false }))).safe_to_retry, true);
  assert.equal(unconfirmed.requests.length, 0);
  const lost = vinted({ keep: false });
  const result = plain(await lost.c.runVintedNative(vintedUpdate()));
  assert.equal(result.error_code, 'uncertain');
  assert.equal(result.safe_to_retry, false);
  assert.equal(lost.sent('PUT', '/api/v2/item_upload/items/42').length, 1);
});

test('a lost Vinted update reply only reads the item', async () => {
  const { c, requests } = vinted({ item: { ...vintedItem(), price: { amount: '24.0' } } });
  assert.equal(plain(await c.runVintedNative(vintedUpdate({ confirm: undefined }), true)).ok, true);
  assert.ok(requests.every(request => request.method === 'GET'));
});

// ---- Sellwild ----

const sellwildItem = () => ({ id: 4512, title: 'Test jacket', text: 'Private description', price: '20.00', categoryId: 272,
  itemCondition: 2, shippable: true, weight: 1.25, depth: 13, width: 10, height: 3, parcelTemplate: '', status: 1, user: { id: 1 },
  photos: ['https://cdn.sellwild.com/a.jpg'] });
const sellwildListing = { title: 'Test jacket', description: 'Private description', price: 24, weight_lb: 1, weight_oz: 4,
  package_dimensions_in: '13x10x3' };

function sellwild({ item = sellwildItem(), keep = true, underReview = false, edit } = {}) {
  const c = loadWorker();
  journaled(c);
  const requests = [];
  let live = item;
  c.fetch = async (url, options = {}) => {
    const route = `${options.method || 'GET'} ${new URL(url).pathname}`;
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
    requests.push({ route, body, credentials: options.credentials });
    if (route === 'GET /api/auth/session') return reply(200, { user: { id: 1 } });
    if (route === 'GET /api/listings/4512') return reply(200, { success: true, listing: live });
    if (route === 'PUT /api/listings/4512/edit') {
      if (edit) return edit(body);
      if (keep) live = { ...live, ...body, price: String(body.price) };
      return reply(200, { success: true, underReview, listing: { id: 4512, title: body.title, price: String(body.price) } });
    }
    return reply(404, { success: false, error: 'missing' });
  };
  const routes = () => requests.map(request => request.route);
  return { c, requests, routes };
}

const sellwildUpdate = extra => ({ job_id: 'job-1', payload: { marketplace: 'sellwild', action: 'update', confirm: true,
  operation_id: 'op-1', listing_id: '4512', listing: sellwildListing, sellwild: { category_id: 272, condition_id: 2 },
  photos: [{ id: 'p1', mime_type: 'image/jpeg' }], ...extra } });

test('a Sellwild update sends the required fields and only the changed ones, without photos, and reads it back', async () => {
  const { c, requests, routes } = sellwild();
  const result = plain(await c.runSellwildNative(sellwildUpdate()));
  assert.deepEqual(result, { ok: true, state: 'published', listing_url: 'https://sellwild.com/product/4512/test-jacket' });
  assert.deepEqual(routes(), ['GET /api/auth/session', 'GET /api/listings/4512', 'PUT /api/listings/4512/edit', 'GET /api/listings/4512']);
  assert.deepEqual(requests[2].body, { title: 'Test jacket', price: 24, categoryId: 272 });
  assert.ok(requests.every(request => request.credentials === 'include'));
});

test('a Sellwild listing that already matches is not written', async () => {
  const { c, routes } = sellwild({ item: { ...sellwildItem(), price: '24.00' } });
  assert.equal(plain(await c.runSellwildNative(sellwildUpdate())).ok, true);
  assert.ok(!routes().includes('PUT /api/listings/4512/edit'));
});

test('a Sellwild edit held for review reports pending review', async () => {
  const { c } = sellwild({ underReview: true });
  assert.equal(plain(await c.runSellwildNative(sellwildUpdate())).state, 'pending_review');
});

test('a Sellwild update refuses another account, a listing that is not live, or no confirmation, before writing', async () => {
  for (const [item, extra] of [[{ ...sellwildItem(), user: { id: 2 } }, {}], [{ ...sellwildItem(), status: 3 }, {}], [sellwildItem(), { confirm: false }]]) {
    const { c, routes } = sellwild({ item });
    const result = plain(await c.runSellwildNative(sellwildUpdate(extra)));
    assert.equal(result.ok, false);
    assert.equal(result.safe_to_retry, true);
    assert.ok(!routes().includes('PUT /api/listings/4512/edit'));
  }
});

test('Sellwild refusing the edit is safe to retry; a read-back that disagrees is unknown', async () => {
  const refused = sellwild({ edit: () => reply(400, { success: false, error: 'Price is too high' }) });
  const first = plain(await refused.c.runSellwildNative(sellwildUpdate()));
  assert.equal(first.safe_to_retry, true);
  const lost = sellwild({ keep: false });
  const second = plain(await lost.c.runSellwildNative(sellwildUpdate()));
  assert.equal(second.error_code, 'uncertain');
  assert.equal(second.safe_to_retry, false);
});

test('a lost Sellwild update reply only reads the listing', async () => {
  const { c, routes } = sellwild({ item: { ...sellwildItem(), price: '24.00' } });
  assert.equal(plain(await c.runSellwildNative(sellwildUpdate({ confirm: undefined, sellwild: undefined }), true)).ok, true);
  assert.deepEqual(routes(), ['GET /api/listings/4512']);
});

// ---- dispatch ----

test('Update needs confirmation and each marketplace’s own listing id before any request', async () => {
  for (const [marketplace, listingId, confirm] of [['mercari', '123', true], ['vinted', 'abc', true], ['sellwild', '0', true],
    ['mercari', 'm12345678', false]]) {
    const c = loadWorker();
    journaled(c);
    c.fetch = async () => assert.fail('no request before the update is confirmed');
    const result = plain(await c.runNativeMarketplace({ payload: { marketplace, action: 'update', confirm, listing_id: listingId } }));
    assert.equal(result.ok, false);
    assert.equal(result.safe_to_retry, true);
    assert.match(result.error, /Confirm updating the live (Mercari|Vinted|Sellwild) listing/);
  }
});
