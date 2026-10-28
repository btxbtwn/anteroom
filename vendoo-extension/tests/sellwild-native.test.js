const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call, reply } = require('./worker-context');

const listing = { title: 'Test jacket', description: 'Private description', price: 24, weight_lb: 1, weight_oz: 4,
  package_dimensions_in: '13x10x3' };
const ADDRESS = { id: 7, isDefault: 1, line1: '1 Private St', locality: 'New Orleans', region: 'LA', zipCode: '70125',
  latitude: '29.96', longitude: '-90.1' };

function worker({ routes = {} } = {}) {
  const c = loadWorker();
  c.AbortSignal = AbortSignal;
  c.Blob = Blob;
  const journal = {};
  c.chrome.storage = { local: { get: async key => ({ [key]: journal[key] }), set: async values => Object.assign(journal, values) } };
  c.chrome.declarativeNetRequest = { updateSessionRules: async () => {} };
  c.chrome.tabs = { create: () => { throw new Error('opened a tab'); }, query: () => { throw new Error('read tabs'); } };
  c.chrome.scripting = { executeScript: () => { throw new Error('ran a page script'); } };
  c.nativePhoto = async () => ({ base64: 'AA==', mime_type: 'image/jpeg' });
  c.sellwildPhotoBlob = async () => new Blob(['jpeg'], { type: 'image/jpeg' });
  const requests = [];
  const defaults = {
    'GET /api/auth/session': [200, { user: { id: 1 } }],
    'GET /api/addresses': [200, { success: true, addresses: [ADDRESS] }],
    'POST /api/listings/create': [200, { success: true, listing: { id: 4512, title: 'Test jacket', price: '24' },
      presignedUrls: [{ photoIndex: 0, size: 'o', presignedUrl: 'https://antengo-listings.s3.amazonaws.com/a?sig=1' },
        { photoIndex: 1, size: 'o', presignedUrl: 'https://antengo-listings.s3.amazonaws.com/b?sig=2' }] }],
    'POST /api/listings/4512/confirm-photos': [200, { success: true }],
    'PUT s3': [200, ''],
  };
  c.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    const route = parsed.hostname === 'sellwild.com' ? `${options.method || 'GET'} ${parsed.pathname}` : `${options.method} s3`;
    requests.push({ route, url, options, body: typeof options.body === 'string' ? JSON.parse(options.body) : options.body });
    const [status, body] = routes[route] || defaults[route] || [404, { success: false, error: 'missing' }];
    return reply(status, body);
  };
  return { c, requests, journal };
}

const message = extra => ({ job_id: 'job-1', payload: { marketplace: 'sellwild', action: 'publish', confirm: true, operation_id: 'op-1',
  listing, sellwild: { category_id: 272, condition_id: 2 },
  photos: [{ id: 'p1', mime_type: 'image/jpeg' }, { id: 'p2', mime_type: 'image/jpeg' }], ...extra } });

test('a confirmed Sellwild publish creates, uploads photos and activates once, with no tab', async () => {
  const { c, requests, journal } = worker();
  const result = JSON.parse(JSON.stringify(await c.runSellwildNative(message())));
  assert.deepEqual(result, { ok: true, state: 'published', listing_url: 'https://sellwild.com/product/4512/test-jacket' });
  assert.deepEqual(requests.map(r => r.route), ['GET /api/auth/session', 'GET /api/addresses', 'POST /api/listings/create',
    'PUT s3', 'PUT s3', 'POST /api/listings/4512/confirm-photos']);
  assert.deepEqual(requests[2].body, { title: 'Test jacket', text: 'Private description', price: 24, categoryId: 272, itemCondition: 2,
    locationText: 'New Orleans, LA', latitude: 29.96, longitude: -90.1, shippable: true, parcelTemplate: '', weight: 1.25,
    depth: 13, width: 10, height: 3, vin: 0, mileage: 0, photos: 2 });
  assert.deepEqual(requests.filter(r => r.route === 'PUT s3').map(r => r.url),
    ['https://antengo-listings.s3.amazonaws.com/a?sig=1', 'https://antengo-listings.s3.amazonaws.com/b?sig=2']);
  assert.ok(requests.filter(r => r.url.startsWith('https://sellwild.com')).every(r => r.options.credentials === 'include'));
  assert.equal(journal['native_operation_op-1'].sellwild_created_id, '4512');
  assert.deepEqual(JSON.parse(JSON.stringify(journal['native_operation_op-1'].sellwild_activation)), result);
});

test('Sellwild applies a chosen discount after the listing is live', async () => {
  const { c, requests } = worker({ routes: { 'POST /api/listings/4512/coupons': [200, { success: true }] } });
  const result = await c.runSellwildNative(message({
    sellwild: { category_id: 272, condition_id: 2, coupon_ids: [117, 'no'] },
  }));
  assert.equal(result.ok, true, result.error);
  assert.equal(result.warning, undefined);
  const post = requests.find(row => row.route === 'POST /api/listings/4512/coupons');
  assert.equal(requests.findIndex(row => row.route === 'POST /api/listings/4512/confirm-photos') < requests.indexOf(post), true);
  assert.deepEqual(post.body, { couponIds: [117] });
});

test('a discount Sellwild refuses leaves the listing live', async () => {
  const { c } = worker({ routes: { 'POST /api/listings/4512/coupons': [500, { success: false }] } });
  const result = await c.runSellwildNative(message({
    sellwild: { category_id: 272, condition_id: 2, coupon_ids: [117] },
  }));
  assert.equal(result.ok, true, result.error);
  assert.equal(result.state, 'published');
  assert.match(result.warning, /discount was not added/);
});

test('Sellwild moderation is reported as pending review', async () => {
  const { c } = worker({ routes: { 'POST /api/listings/4512/confirm-photos': [200, { success: true, underReview: true }] } });
  assert.equal((await c.runSellwildNative(message())).state, 'pending_review');
});

test('Sellwild packaging is sent with native dimensions and description remains optional', async () => {
  const { c, requests } = worker();
  const result = await c.runSellwildNative(message({
    listing: { ...listing, description: '', weight_lb: 0, weight_oz: 0, package_dimensions_in: '' },
    sellwild: { category_id: 246, condition_id: 3, parcel_template: 'USPS_FlatRatePaddedEnvelope', fixed_package: true,
      package_dimensions: { depth: 13, width: 10, height: 1 } },
  }));
  assert.equal(result.ok, true);
  const body = requests.find(row => row.route === 'POST /api/listings/create').body;
  assert.equal(body.text, '');
  assert.equal(body.parcelTemplate, 'USPS_FlatRatePaddedEnvelope');
  assert.deepEqual([body.depth, body.width, body.height], [13, 10, 1]);
  assert.equal(body.weight, undefined);
});

test('Sellwild publishing needs confirmation and complete fields before any request', async () => {
  for (const extra of [{ confirm: false }, { action: 'review' }, { sellwild: { category_id: 272 } },
    { listing: { ...listing, price: 4 } }, { listing: { ...listing, weight_lb: 0, weight_oz: 0 } },
    { listing: { ...listing, package_dimensions_in: '' } }, { photos: [] }]) {
    const { c, requests } = worker();
    const result = await c.runSellwildNative(message(extra));
    assert.equal(result.ok, false);
    assert.equal(result.safe_to_retry, true, JSON.stringify(extra));
    assert.equal(requests.length, 0);
  }
});

test('a missing session or ship-from address stops before creating anything', async () => {
  for (const routes of [{ 'GET /api/auth/session': [200, {}] }, { 'GET /api/addresses': [200, { success: true, addresses: [] }] },
    { 'GET /api/addresses': [200, { success: true, addresses: [{ ...ADDRESS, line1: '' }] }] }, { 'GET /api/auth/session': [401, {}] }]) {
    const { c, requests } = worker({ routes });
    const result = await c.runSellwildNative(message());
    assert.equal(result.safe_to_retry, true);
    assert.ok(!requests.some(r => r.route === 'POST /api/listings/create'));
  }
});

test('a rejected create can be retried; a lost create reply cannot', async () => {
  let { c } = worker({ routes: { 'POST /api/listings/create': [400, { success: false, error: 'Price must be less than 1,000,000' }] } });
  let result = await c.runSellwildNative(message());
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.error_code, 'rejected');
  assert.equal(result.marketplace_message, 'Price must be less than 1,000,000');
  ({ c } = worker({ routes: { 'POST /api/listings/create': [502, null] } }));
  result = await c.runSellwildNative(message());
  assert.equal(result.safe_to_retry, false);
});

test('a failed photo upload or activation leaves an inactive listing that is never retried', async () => {
  for (const routes of [{ 'PUT s3': [403, ''] }, { 'POST /api/listings/4512/confirm-photos': [500, { success: false, error: 'Failed to activate listing' }] }]) {
    const { c, requests } = worker({ routes });
    const result = await c.runSellwildNative(message());
    assert.equal(result.ok, false);
    assert.equal(result.safe_to_retry, false);
    assert.match(result.error, /listing 4512 exists but is not active/);
    assert.equal(requests.filter(r => r.route === 'POST /api/listings/create').length, 1);
  }
});

test('recovery only reads the journal and never writes to Sellwild', async () => {
  const { c, requests, journal } = worker();
  assert.match((await c.runSellwildNative(message(), true)).error, /did not confirm a listing/);
  journal['native_operation_op-1'] = { sellwild_created_id: '4512' };
  assert.match((await c.runSellwildNative(message(), true)).error, /created listing 4512/);
  journal['native_operation_op-1'].sellwild_activation = { ok: true, state: 'published', listing_url: 'https://sellwild.com/product/4512/test-jacket' };
  assert.equal((await c.runSellwildNative(message(), true)).state, 'published');
  assert.equal(requests.length, 0);
});

test('weight and box helpers follow Sellwild units', () => {
  const { c } = worker();
  assert.equal(c.sellwildWeightLb({ weight_lb: 0, weight_oz: 10 }), 0.63);
  assert.equal(c.sellwildWeightLb({}), null);
  assert.deepEqual({ ...c.sellwildBox({ package_dimensions_in: '12 x 9 x 5' }) }, { depth: 12, width: 9, height: 5 });
  assert.equal(c.sellwildBox({ package_dimensions_in: '12x9' }), null);
});

test('Sellwild Shipping From is the address its sell page shows, printed as its card', () => {
  const c = loadWorker();
  const address = (id, isDefault, extra = {}) => ({ id, isDefault, fullName: 'Pat Seller', line1: `${id} Example St`, line2: '',
    locality: 'Springfield', region: 'IL', zipCode: '62701', ...extra });
  // The page reverses the list, then takes the default, otherwise the newest.
  assert.equal(call(c, 'sellwildShipFrom', [address(1, 1), address(2, 0), address(3, 1)]).id, 3);
  assert.equal(call(c, 'sellwildShipFrom', [address(1, 0), address(2, 0)]).id, 2);
  assert.equal(call(c, 'sellwildShipFromText', [address(7, 1, { line2: 'Apt 4' })]),
    'Pat Seller\n7 Example St\nApt 4\nSpringfield, IL, 62701\nUnited States of America');
  assert.equal(call(c, 'sellwildShipFromText', []), '');
  // Direct List sends the same address's city and state.
  assert.equal(call(c, 'sellwildAddress', [address(1, 1, { locality: 'Peoria' }), address(2, 1)]).locationText, 'Springfield, IL');
});
