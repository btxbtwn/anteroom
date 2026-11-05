// Update of a live Mercari listing: one seller-confirmed
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
  direct_category_ids: { mercari: '303' }, zipCode: '70125', weight_lb: 0, weight_oz: 10,
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

// ---- dispatch ----

test('Update needs confirmation and each marketplace’s own listing id before any request', async () => {
  for (const [marketplace, listingId, confirm] of [['mercari', '123', true],
    ['mercari', 'm12345678', false]]) {
    const c = loadWorker();
    journaled(c);
    c.fetch = async () => assert.fail('no request before the update is confirmed');
    const result = plain(await c.runNativeMarketplace({ payload: { marketplace, action: 'update', confirm, listing_id: listingId } }));
    assert.equal(result.ok, false);
    assert.equal(result.safe_to_retry, true);
    assert.match(result.error, /Confirm updating the live Mercari listing/);
  }
});
