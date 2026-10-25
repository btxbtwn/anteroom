const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

const listing = { title: 'Test jacket', description: 'Private draft', price: 24.6, condition: 'Pre-Owned - Good',
  marketplace_category_ids: { mercari: '303' }, zipCode: '70125', weight_lb: 0, weight_oz: 10 };
const SESSION = { csrf: 'private-csrf', accessToken: 'private-session', user: { userId: 7, name: 'seller', email: 'seller@example.com' } };
const INITIALIZE = 'https://www.mercari.com/v1/initialize';

function worker({ session = SESSION, respond, http } = {}) {
  const c = loadWorker();
  const journal = {};
  c.chrome.storage = {
    local: { get: async key => ({ [key]: journal[key] }), set: async values => Object.assign(journal, values), remove: async () => {} },
  };
  c.chrome.declarativeNetRequest = { updateSessionRules: async () => {} };
  c.chrome.tabs = { create: () => { throw new Error('opened a tab'); }, query: () => { throw new Error('read tabs'); } };
  c.nativePhoto = async () => ({ base64: 'AA==', mime_type: 'image/jpeg' });
  c.mercariPhotoBlob = async () => new Blob(['jpeg'], { type: 'image/jpeg' });
  const requests = [];
  const initializes = [];
  let images = 0;
  let draft = { name: 'Test jacket', description: 'Private draft', price: 2500, itemCondition: { id: 3 }, photos: [{ id: 'i1' }, { id: 'i2' }] };
  c.fetch = async (url, options) => {
    if (url === INITIALIZE) {
      initializes.push(options);
      return typeof session === 'function' ? session(initializes.length) : reply(200, session || { csrf: 'anonymous', user: {} });
    }
    const body = options.body instanceof FormData ? { multipart: options.body } : JSON.parse(options.body);
    requests.push({ url, headers: options.headers, credentials: options.credentials, body });
    const op = body.multipart ? JSON.parse(body.multipart.get('operations')).operationName : body.operationName;
    if (http) { const answer = http(op, requests.length); if (answer) return answer; }
    const data = respond ? respond(op, body) : {
      uploadDraftItemImageMutation: { uploadDraftItemImage: { images: [{ id: `i${++images}` }] } },
      addDraftItemMutation: { addDraftItem: { item: { id: 'm9AbC123', draftItemStatus: 'action_required' } } },
      editDraftItemMutation: { editDraftItem: { id: 'm9AbC123' } },
      draftItemQuery: { draftItem: draft },
    }[op];
    return reply(200, data && data.errors ? data : { data });
  };
  const opsOf = () => requests.map(r => (r.body.multipart ? JSON.parse(r.body.multipart.get('operations')).operationName : r.body.operationName));
  return { c, requests, initializes, journal, opsOf, setDraft: value => { draft = value; } };
}

const message = extra => ({ job_id: 'job-1', payload: { marketplace: 'mercari', action: 'save_draft', operation_id: 'op-1',
  listing, photos: [{ id: 'p1', mime_type: 'image/jpeg' }, { id: 'p2', mime_type: 'image/jpeg' }], ...extra } });

test('a new Mercari draft uploads photos, saves once, and is confirmed by reading it back, with no tab', async () => {
  const { c, requests, initializes, journal, opsOf } = worker();
  const result = await c.runMercariNative(message());
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, state: 'draft', draft_url: 'https://www.mercari.com/sell/draft/m9AbC123/' });
  assert.deepEqual(opsOf(), ['uploadDraftItemImageMutation', 'uploadDraftItemImageMutation', 'addDraftItemMutation', 'draftItemQuery']);
  assert.equal(initializes.length, 1);
  assert.equal(initializes[0].credentials, 'include');
  const upload = requests[0].body.multipart;
  assert.deepEqual(JSON.parse(upload.get('map')), { 0: ['variables.input.photos.0'] });
  assert.deepEqual(JSON.parse(upload.get('operations')).variables, { input: { photos: [null] } });
  assert.deepEqual(requests[2].body.variables, { input: { common: { name: 'Test jacket', isAutoPriceDrop: false, description: 'Private draft', price: 2500, categoryId: 303, conditionId: 3, shipping: { payerId: 1, zipCode: '70125', dimensions: {}, weight: { weight: 10 } }, imageIds: ['i1', 'i2'] } } });
  assert.equal(requests[2].body.extensions.persistedQuery.sha256Hash, '2b7de5aa72261ef11ab70a37cd06eb470bf6c57097d3e9f346f57025c4fc9a97');
  assert.deepEqual(requests[3].body.variables, { input: { draftItemId: 'm9AbC123' } });
  assert.ok(requests.every(r => r.url === 'https://www.mercari.com/v1/api' && r.headers['x-csrf-token'] === 'private-csrf'
    && r.headers.authorization === 'Bearer private-session'));
  assert.ok(requests.every(r => !('x-socure-device-token' in r.headers)));
  assert.equal(journal['native_operation_op-1'].receipt_url, 'https://www.mercari.com/sell/draft/m9AbC123/');
  assert.ok(!JSON.stringify(result).includes('private'));
  assert.equal(journal.mercari_session, undefined);
});

test('an expired Mercari session is read again from initialize and the refused request sent again', async () => {
  let refusals = 0;
  const { c, initializes, opsOf } = worker({
    session: n => reply(200, { ...SESSION, accessToken: `token-${n}` }),
    http: (op, n) => (op === 'addDraftItemMutation' && refusals++ < 1 ? reply(200, { errors: [{ message: 'Unauthorized' }] }) : null),
  });
  const result = await c.runMercariNative(message());
  assert.equal(result.ok, true);
  assert.equal(initializes.length, 2);
  assert.deepEqual(opsOf().filter(op => op === 'addDraftItemMutation').length, 2);
});

test('Mercari gives up after two session refreshes and reports a reconnect that is safe to retry', async () => {
  const { c, initializes } = worker({ http: op => (op === 'addDraftItemMutation' ? reply(401, { errors: [{ message: 'Unauthorized' }] }) : null) });
  const result = await c.runMercariNative(message());
  assert.equal(result.ok, false);
  assert.equal(result.error_code, 'signed_out');
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.marketplace_message, 'Unauthorized');
  assert.equal(initializes.length, 3);
});

test('a Mercari bot check on HTTP 403 is a challenge, before anything is written', async () => {
  const page = '<html><title>Just a moment...</title><div id="challenge-error-text"></div></html>';
  const { c, requests } = worker({ session: () => reply(403, page, { 'content-type': 'text/html' }) });
  const result = await c.runMercariNative(message());
  assert.equal(result.error_code, 'challenge');
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.marketplace_message, 'HTTP 403');
  assert.equal(requests.length, 0);
});

test('a refused Mercari draft save passes Mercari\'s own words through and can be retried', async () => {
  const { c } = worker({ respond: op => (op === 'addDraftItemMutation'
    ? { errors: [{ message: 'Please enter a valid zip code.', extensions: { code: 'BAD_USER_INPUT' } }] }
    : { uploadDraftItemImageMutation: { uploadDraftItemImage: { images: [{ id: 'i1' }] } } }[op]) });
  const result = await c.runMercariNative(message());
  assert.equal(result.error_code, 'rejected');
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.marketplace_message, 'Please enter a valid zip code.');
});

test('a transient Mercari read is retried, a draft save never is', async () => {
  let reads = 0;
  const { c, opsOf } = worker({ http: op => (op === 'draftItemQuery' && reads++ < 1 ? reply(503, 'busy') : null) });
  assert.equal((await c.runMercariNative(message())).ok, true);
  assert.equal(opsOf().filter(op => op === 'draftItemQuery').length, 2);
  const lost = worker({ http: op => (op === 'addDraftItemMutation' ? reply(502, 'bad gateway') : null) });
  const result = await lost.c.runMercariNative(message());
  assert.equal(result.error_code, 'uncertain');
  assert.equal(result.safe_to_retry, false);
  assert.equal(lost.opsOf().filter(op => op === 'addDraftItemMutation').length, 1);
});

test('Mercari read-back ignores line endings and spacing Mercari rewrites', () => {
  const c = loadWorker();
  assert.equal(c.mercariDraftMatches({ name: 'Test  jacket ', description: 'Line one\r\nLine two ', price: 2500, photo_count: 1 },
    { ...listing, title: 'Test jacket', description: 'Line one\nLine two' }, 1), true);
});

test('an existing Mercari draft is edited in place without re-uploading photos', async () => {
  const { c, requests, opsOf } = worker();
  const result = await c.runMercariNative(message({ draft_url: 'https://www.mercari.com/sell/draft/m9AbC123/' }));
  assert.equal(result.ok, true);
  assert.deepEqual(opsOf(), ['editDraftItemMutation', 'draftItemQuery']);
  assert.deepEqual(requests[0].body.variables, { input: { id: 'm9AbC123', common: { name: 'Test jacket', isAutoPriceDrop: false, description: 'Private draft', price: 2500, categoryId: 303, conditionId: 3, shipping: { payerId: 1, zipCode: '70125', dimensions: {}, weight: { weight: 10 } } } } });
});

test('Mercari asks for a reconnect when initialize has no signed-in user, before any request', async () => {
  const { c, requests } = worker({ session: null });
  const result = await c.runMercariNative(message());
  assert.match(result.error, /reconnect it in Anteroom/);
  assert.equal(result.error_code, 'signed_out');
  assert.equal(result.safe_to_retry, true);
  assert.equal(requests.length, 0);
});

test('a lost reply after the draft save is never retried', async () => {
  const { c, requests } = worker();
  const original = c.fetch;
  c.fetch = async (url, options) => {
    if (url !== INITIALIZE && !(options.body instanceof FormData) && JSON.parse(options.body).operationName === 'addDraftItemMutation') {
      requests.push({}); throw new Error('Disconnected');
    }
    return original(url, options);
  };
  const result = await c.runMercariNative(message());
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, false);
});

test('a draft that Mercari saved differently is reported, not accepted', async () => {
  const { c, setDraft } = worker();
  setDraft({ name: 'Test jacket', description: 'Private draft', price: 2500, itemCondition: { id: 3 }, photos: [{ id: 'i1' }] });
  const result = await c.runMercariNative(message());
  assert.equal(result.ok, false);
  assert.match(result.error, /did not keep/);
  assert.equal(result.error_code, 'uncertain');
});

test('a changed Mercari form is reported as needing an update', async () => {
  const { c } = worker({ respond: () => ({ errors: [{ message: 'PersistedQueryNotFound' }] }) });
  const result = await c.runMercariNative(message());
  assert.match(result.error, /changed its listing form/);
  assert.equal(result.error_code, 'schema_drift');
  assert.equal(result.marketplace_message, 'PersistedQueryNotFound');
  assert.equal(result.safe_to_retry, true);
});

test('Mercari prices are whole dollars, sent in cents', () => {
  const c = loadWorker();
  assert.equal(c.mercariPriceCents(24.6), 2500);
  assert.equal(c.mercariPriceCents('13'), 1300);
  assert.equal(c.mercariPriceCents(0), null);
  assert.equal(c.mercariPriceCents(''), null);
});

test('conditions map to Mercari IDs, and worn items never become Like new', () => {
  const c = loadWorker();
  for (const [condition, id] of [
    ['New With Tags/Box', 1], ['New Without Tags/Box', 2], ['New With Imperfections', 3], ['Pre-Owned - Excellent', 3],
    ['Pre-Owned - Good', 3], ['Pre-Owned - Fair', 4], ['Poor (Major flaws)', 5], ['Like new (New without tags)', 2],
    ['Good (Gently used)', 3], ['', null],
  ]) assert.equal(c.mercariConditionId(condition), id, condition);
});

test('the Mercari category comes from the listing, or is left for Mercari to ask', () => {
  const c = loadWorker();
  assert.equal(c.mercariCategoryId({ marketplace_category_ids: { mercari: '303' } }), 303);
  assert.equal(c.mercariCategoryId({ mercari_specifics: { categoryId: '27' } }), 27);
  assert.equal(c.mercariCategoryId({ marketplace_category_ids: { mercari: 'Men > Tops' } }), null);
});

test('Mercari needs a package weight, sent in whole ounces', async () => {
  const c = loadWorker();
  assert.equal(c.mercariWeightOz({ weight_lb: 1, weight_oz: 2.2 }), 19);
  assert.equal(c.mercariWeightOz({ weight_oz: 8 }), 8);
  assert.equal(c.mercariWeightOz({}), null);
  assert.equal(c.mercariWeightOz({ form: { overrides: { weight: { pounds: '0', ounces: '8' } } } }), 8);
  const { c: w, requests } = worker();
  const result = await w.runMercariNative(message({ listing: { ...listing, weight_lb: 0, weight_oz: 0 } }));
  assert.match(result.error, /package weight/);
  assert.equal(result.error_code, 'missing_fields');
  assert.deepEqual(JSON.parse(JSON.stringify(result.fields)), [{ field: 'weight', message: 'Mercari needs a package weight' }]);
  assert.equal(result.safe_to_retry, true);
  assert.equal(requests.length, 0);
});

const DRAFT_URL = 'https://www.mercari.com/sell/draft/m9AbC123/';
const publish = extra => message({ action: 'publish', confirm: true, draft_url: DRAFT_URL, ...extra });

test('a READY Mercari draft is listed with one request, the way its drafts page does, with no tab', async () => {
  const { c, requests, journal, opsOf } = worker({ respond: op => ({
    draftItemQuery: { draftItem: { name: 'Test jacket', description: 'Private draft', price: 2500, itemCondition: { id: 3 },
      photos: [{ id: 'i1' }], draftItemStatus: 'DRAFT_ITEM_STATUS_READY', draftItemActionsRequired: [] } },
    listDraftItemMutation: { listDraftItem: 'm12345678901' },
  }[op]) });
  const result = await c.runMercariNative(publish());
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, state: 'published', draft_url: DRAFT_URL,
    listing_url: 'https://www.mercari.com/us/item/m12345678901/' });
  assert.deepEqual(opsOf(), ['draftItemQuery', 'listDraftItemMutation']);
  assert.deepEqual(requests[1].body.variables, { input: { draftItemId: 'm9AbC123' } });
  assert.equal(journal['native_operation_op-1'].listing_url, 'https://www.mercari.com/us/item/m12345678901/');
});

test('publishing an incomplete Mercari draft says what Mercari still needs, without listing it', async () => {
  const { c, opsOf } = worker({ respond: op => ({
    draftItemQuery: { draftItem: { name: 'Test jacket', description: 'Private draft', price: 2500, photos: [{ id: 'i1' }],
      draftItemStatus: 'DRAFT_ITEM_STATUS_ACTION_REQUIRED', draftItemActionsRequired: [{ field: 'brandId' }, { field: 'shippingClassIds' }] } },
  }[op]) });
  const result = await c.runMercariNative(publish());
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, true);
  assert.match(result.error, /brand, shipping/);
  assert.equal(result.error_code, 'missing_fields');
  assert.equal(result.draft_url, DRAFT_URL);
  assert.deepEqual(JSON.parse(JSON.stringify(result.fields)), [{ field: 'brandId', message: 'Mercari needs a brand' },
    { field: 'shippingClassIds', message: 'Mercari needs shipping' }]);
  assert.equal(result.marketplace_message, 'draftItemActionsRequired: brandId, shippingClassIds');
  assert.deepEqual(opsOf(), ['draftItemQuery']);
});

test('Mercari publishing needs the seller to confirm, and sends nothing otherwise', async () => {
  const { c, requests } = worker();
  const result = await c.runMercariNative(publish({ confirm: false }));
  assert.match(result.error, /Confirm publishing/);
  assert.equal(requests.length, 0);
});

test('a lost reply after the Mercari publish is never retried, and recovery uses only the receipt', async () => {
  const { c, journal } = worker({ respond: op => {
    if (op === 'listDraftItemMutation') throw new Error('Disconnected');
    return { draftItemQuery: { draftItem: { name: 'Test jacket', description: 'Private draft', price: 2500, photos: [{}],
      draftItemStatus: 'DRAFT_ITEM_STATUS_READY', draftItemActionsRequired: [] } } }[op];
  } });
  const result = await c.runMercariNative(publish());
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, false);
  const recovered = await c.runMercariNative(publish(), true);
  assert.equal(recovered.ok, false);
  journal['native_operation_op-1'] = { listing_url: 'https://www.mercari.com/us/item/m12345678901/' };
  const confirmed = await c.runMercariNative(publish(), true);
  assert.equal(confirmed.state, 'published');
});

// Boys tops & t-shirts > Boys (4+), from Mercari's size master (read 2026-10-25).
const BOYS_SIZES = [[101, 'XS (4-5)', 'XS'], [102, 'S (6-7)', 'S'], [103, 'M (8)', 'M'], [104, 'L (10-12)', 'L'],
  [105, 'XL (14-16)', 'XL'], [168, '2XL (18)', '2XL'], [169, 'One Size', 'One Size']].map(([id, name, title]) => ({ id, name, title, group: 83 }));
const MEN_TOP_SIZES = [[11, 'L (42-44)', 'L', 20], [12, 'XL (46-48)', 'XL', 20], [13, 'XXL (50-52)', 'XXL', 20], [342, 'XL', 'XL', 21],
  [346, 'Big 2X', 'Big 2X', 22]].map(([id, name, title, group]) => ({ id, name, title, group }));
const clothing = extra => ({ title: 'Boys graphic tee', description: 'Soft cotton tee', price: 18.4, brand: 'Nike', size: 'XL (18/20)',
  condition: 'Pre-Owned - Good', weight_lb: 0, weight_oz: 7, ...extra,
  form: { overrides: { condition: '3', brand: 'Nike', categoryV2: { id: '1934', path: ['3', '1885', '1934'], hasChildren: false } },
    specifics: { shipping: { payerId: 1, carrierId: 2508, location: '70115', deliveryMethod: 'mercari_shipping' } },
    aspects: {}, sizes: BOYS_SIZES, ships_from_zip: '70115', ...extra?.form } });

function withVendooBrand(c, journal, answer) {
  journal.vendoo_session = { uid: 'u1', refresh_token: 'private-refresh', access_token: 'private-vendoo', expiration_time: Date.now() + 3600000 };
  const original = c.fetch;
  const asked = [];
  c.fetch = async (url, options = {}) => {
    if (String(url).startsWith('https://api.web.vendoo.co/')) { asked.push(String(url)); return reply(...answer); }
    return original(url, options);
  };
  return asked;
}

test('a full Mercari clothing listing sends its category, size, brand, label, payer and ZIP', async () => {
  const { c, requests, journal, setDraft } = worker();
  setDraft({ name: 'Boys graphic tee', description: 'Soft cotton tee', price: 1800, itemCondition: { id: 3 }, photos: [{ id: 'i1' }, { id: 'i2' }] });
  const asked = withVendooBrand(c, journal, [200, [{ value: '5813', label: 'Nike' }]]);
  const result = await c.runMercariNative(message({ listing: clothing() }));
  assert.equal(result.ok, true);
  const common = requests.find(r => r.body.operationName === 'addDraftItemMutation').body.variables.input.common;
  assert.deepEqual(JSON.parse(JSON.stringify(common)), { name: 'Boys graphic tee', isAutoPriceDrop: false, description: 'Soft cotton tee',
    price: 1800, categoryId: 1934, conditionId: 3, sizeId: 105,
    shipping: { payerId: 1, zipCode: '70115', dimensions: {}, weight: { weight: 7 } }, shippingClassIds: [2508], brandId: 5813,
    imageIds: ['i1', 'i2'] });
  assert.match(asked[0], /\/api\/rest\/v1\/brand\/mercari\?search=Nike&matchValue=true$/);
});

test('a stored Mercari category is the one that is listed, and the Vendoo id stays put', () => {
  const { c } = worker();
  const listing = clothing();
  listing.direct_category_ids = { mercari: '2001' };
  listing.marketplace_category_ids = { mercari: '1934' };
  assert.deepEqual(JSON.parse(JSON.stringify(c.mercariCategory(listing))), { id: 2001, parent: false });
  assert.equal(listing.marketplace_category_ids.mercari, '1934');
});

test('Mercari sizes come from the category and never cross a fit the listing names', () => {
  const { c } = worker();
  const size = (listing, sizes = BOYS_SIZES) => JSON.parse(JSON.stringify(c.mercariSize({ ...listing, form: { aspects: {}, sizes } }, 1934)));
  assert.deepEqual(size({ size: 'XL (18/20)' }), { id: 105 });
  assert.deepEqual(size({ size: '2XL' }), { id: 168 });
  assert.deepEqual(size({ size: 'XL' }, MEN_TOP_SIZES), { id: 12 });
  assert.match(size({ size: 'XL', sizeType: 'Big & Tall' }, MEN_TOP_SIZES).problem, /XL \(46-48\) or XL/);
  assert.deepEqual(size({ size: 'L/XL' }), { problem: 'a size Mercari offers in this category' });
  assert.deepEqual(JSON.parse(JSON.stringify(c.mercariSize({ size: 'M', form: { aspects: { '1934_Size': '103' }, sizes: BOYS_SIZES } }, 1934))), { id: 103 });
  assert.deepEqual(size({ size: 'M' }, []), { none: 'none' });
});

test('a Mercari listing Mercari would refuse stops before any upload and names each field', async () => {
  for (const [listing, field] of [[clothing({ size: 'L/XL' }), 'sizeId'], [clothing({ price: 2400 }), 'price'], [clothing({ price: 0.4 }), 'price'],
    [clothing({ description: 'x'.repeat(1001) }), 'description'], [clothing({ weight_lb: 51 }), 'weight'],
    [clothing({ form: { overrides: { condition: '3', categoryV2: { id: '1885', hasChildren: true } } } }), 'categoryId']]) {
    const { c, requests } = worker();
    const result = await c.runMercariNative(message({ listing }));
    assert.equal(result.error_code, 'missing_fields', field);
    assert.equal(result.safe_to_retry, true);
    assert.ok(result.fields.some(entry => entry.field === field), field);
    assert.equal(requests.length, 0, field);
  }
});

test('a brand Vendoo\'s Mercari index cannot place is left for Mercari\'s own check', async () => {
  const { c, requests, journal } = worker();
  withVendooBrand(c, journal, [503, {}]);
  await c.runMercariNative(message({ listing: clothing() }));
  const common = requests.find(r => r.body.operationName === 'addDraftItemMutation').body.variables.input.common;
  assert.equal(common.brandId, undefined);
});

test('a Mercari draft input it no longer accepts is reported as a form change, safe to retry', async () => {
  const { c } = worker({ respond: op => (op === 'addDraftItemMutation'
    ? { errors: [{ message: 'Field "sizeId" is not defined by type "DraftItemCommonInput".', extensions: { code: 'GRAPHQL_VALIDATION_FAILED' } }] }
    : { uploadDraftItemImage: { images: [{ id: 'i1' }] } }) });
  const result = await c.runMercariNative(message({ listing: clothing() }));
  assert.equal(result.error_code, 'schema_drift');
  assert.equal(result.safe_to_retry, true);
  assert.match(result.marketplace_message, /sizeId/);
});
