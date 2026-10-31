const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

const listing = { title: 'Test jacket', description: 'Private draft', price: 24.6, condition: 'Pre-Owned - Good',
  direct_category_ids: { mercari: '303' }, zipCode: '70125', weight_lb: 0, weight_oz: 10,
  form: { specifics: { shipping: { payerId: 1, carrierId: '2509' } } } };
// What Mercari's draftItemQuery answers for a draft that kept the label and payer.
const KEPT = { itemCondition: { id: 3 }, itemCategory: { id: 303 }, shippingPayer: { id: 1 }, shippingClasses: [{ id: 2509 }] };
const SHIPPING = { payerId: 1, classIds: [2509], suggestedClassIds: [2509], zipCode: '70125', dimensions: {}, weight: { weight: 10 } };

test('ship on your own uses Mercari’s zero class sentinel and seller payer without a label lookup', async () => {
  const c = loadWorker();
  const ownListing = { ...listing, mercari_specifics: { shippingMethod: 'SOYO', shippingPayerId: 1 }, weight_oz: 0 };
  c.mercariRequest = async () => assert.fail('SOYO does not need prepaid label rates');
  assert.equal((await c.mercariShippingClasses(ownListing, Date.now() + 30000)).length, 0);
  const common = c.mercariCommon(ownListing);
  assert.equal(common.shipping.payerId, 2);
  assert.deepEqual(Array.from(common.shipping.classIds), [0]);
  assert.deepEqual(Array.from(common.shipping.suggestedClassIds), []);
  assert.equal(common.shipping.dimensions.unit, undefined);
  assert.ok(!c.mercariMissing(ownListing, common).some(row => /weight|shippingClassIds/.test(row.field)));
  const draft = { ...common, photo_count: 1, payerId: 2, classIds: [], isAutoPriceDrop: false };
  assert.ok(!c.mercariDraftDifferences(draft, common, 1).includes('shipping'));
  draft.classIds = [2509];
  assert.ok(c.mercariDraftDifferences(draft, common, 1).includes('shipping'));
});
const SESSION = { csrf: 'private-csrf', accessToken: 'private-session', user: { userId: 7, name: 'seller', email: 'seller@example.com' } };
const INITIALIZE = 'https://www.mercari.com/v1/initialize';

function worker({ session = SESSION, respond, http, prepareCatalog = false } = {}) {
  const c = loadWorker();
  const journal = {};
  c.chrome.storage = {
    local: { get: async key => ({ [key]: journal[key] }), set: async values => Object.assign(journal, values), remove: async () => {} },
  };
  c.chrome.declarativeNetRequest = { updateSessionRules: async () => {} };
  c.chrome.tabs = { create: () => { throw new Error('opened a tab'); }, query: () => { throw new Error('read tabs'); } };
  c.nativePhoto = async () => ({ base64: 'AA==', mime_type: 'image/jpeg' });
  c.mercariPhotoBlob = async () => new Blob(['jpeg'], { type: 'image/jpeg' });
  // These mutation tests isolate the already-tested catalog preparation.
  if (!prepareCatalog) c.mercariPreparedListing = async value => value;
  const requests = [];
  const initializes = [];
  let images = 0;
  let draft = { name: 'Test jacket', description: 'Private draft', price: 2500, ...KEPT, photos: [{ id: 'i1' }, { id: 'i2' }] };
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
  assert.deepEqual(requests[2].body.variables, { input: { common: { name: 'Test jacket', isAutoPriceDrop: false, description: 'Private draft', price: 2500, categoryId: 303, conditionId: 3, shipping: SHIPPING, imageIds: ['i1', 'i2'] } } });
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
  const common = c.mercariCommon({ ...listing, title: 'Test jacket', description: 'Line one\nLine two' });
  assert.equal(c.mercariDraftMatches({ name: 'Test  jacket ', description: 'Line one\r\nLine two ', price: 2500, photo_count: 1,
    categoryId: 303, conditionId: 3, payerId: 1, classIds: [2509] }, common, 1), true);
});

test('an existing Mercari draft sends its photos when edited so Mercari does not clear them', async () => {
  const { c, requests, opsOf } = worker();
  const result = await c.runMercariNative(message({ draft_url: 'https://www.mercari.com/sell/draft/m9AbC123/' }));
  assert.equal(result.ok, true);
  assert.deepEqual(opsOf(), ['uploadDraftItemImageMutation', 'uploadDraftItemImageMutation', 'editDraftItemMutation', 'draftItemQuery']);
  assert.deepEqual(requests[2].body.variables, { input: { id: 'm9AbC123', common: { name: 'Test jacket', isAutoPriceDrop: false, description: 'Private draft', price: 2500, categoryId: 303, conditionId: 3, shipping: SHIPPING, imageIds: ['i1', 'i2'] } } });
});

test('an existing Mercari draft that loses photos is not reported as saved', async () => {
  const { c, setDraft } = worker();
  setDraft({ ...KEPT, name: 'Test jacket', description: 'Private draft', price: 2500, photos: [] });
  const result = await c.runMercariNative(message({ draft_url: 'https://www.mercari.com/sell/draft/m9AbC123/' }));
  assert.equal(result.ok, false);
  assert.match(result.error, /photos/);
  assert.equal(result.safe_to_retry, false);
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
  setDraft({ name: 'Test jacket', description: 'Private draft', price: 2500, ...KEPT, photos: [{ id: 'i1' }] });
  const result = await c.runMercariNative(message());
  assert.equal(result.ok, false);
  assert.match(result.error, /did not keep everything this listing sent \(photos\)/);
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

test('the Mercari category is the one Anteroom stored, never Vendoo\'s', () => {
  const c = loadWorker();
  assert.equal(c.mercariCategoryId({ direct_category_ids: { mercari: '303' } }), 303);
  assert.equal(c.mercariCategoryId({ direct_category_ids: { mercari: 'Men > Tops' } }), null);
  assert.equal(c.mercariCategoryId({ marketplace_category_ids: { mercari: '303' } }), null);
  assert.equal(c.mercariCategoryId({ mercari_specifics: { categoryId: '27' } }), null);
  assert.equal(c.mercariCategoryId({ form: { overrides: { categoryV2: { id: '303' } } } }), null);
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
    draftItemQuery: { draftItem: { name: 'Test jacket', description: 'Private draft', price: 2500, ...KEPT,
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
    draftItemQuery: { draftItem: { name: 'Test jacket', description: 'Private draft', price: 2500, ...KEPT, photos: [{ id: 'i1' }],
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
    return { draftItemQuery: { draftItem: { name: 'Test jacket', description: 'Private draft', price: 2500, ...KEPT, photos: [{}],
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
  condition: 'Pre-Owned - Good', weight_lb: 0, weight_oz: 7, direct_category_ids: { mercari: '1934' }, ...extra,
  form: { overrides: { condition: '3', brand: 'Nike', categoryV2: { id: '1934', path: ['3', '1885', '1934'], hasChildren: false } },
    specifics: { shipping: { payerId: 1, carrierId: 2508, location: '70115', deliveryMethod: 'mercari_shipping' } },
    aspects: {}, sizes: BOYS_SIZES, ships_from_zip: '70115', ...extra?.form } });

function withVendooBrand(c, journal, answer) {
  const asked = [];
  c.mercariCatalogData = async () => {
    asked.push('masterQuery');
    if (answer[0] !== 200) throw new Error('Mercari catalog unavailable');
    return { itemBrands: answer[1].map(row => ({ id: row.value, name: row.label })) };
  };
  return asked;
}

// The draft Mercari keeps for clothing(): what draftItemQuery answers.
const CLOTHING_DRAFT = { name: 'Boys graphic tee', description: 'Soft cotton tee', price: 1800, itemCondition: { id: 3 },
  itemCategory: { id: 1934 }, brand: { id: 5813 }, itemSize: { id: 168 }, shippingPayer: { id: 1 }, shippingClasses: [{ id: 2508 }],
  photos: [{ id: 'i1' }, { id: 'i2' }] };

test('a full Mercari clothing listing sends its category, size, brand, label inside shipping, payer and ZIP, and no box until the shoebox question is answered', async () => {
  const { c, requests, journal, setDraft } = worker();
  setDraft(CLOTHING_DRAFT);
  const asked = withVendooBrand(c, journal, [200, [{ value: '9', label: 'Nike SB' }, { value: '5813', label: 'NIKE' }]]);
  const result = await c.runMercariNative(message({ listing: clothing({ package_dimensions_in: '13x10x3' }) }));
  assert.equal(result.ok, true);
  const common = requests.find(r => r.body.operationName === 'addDraftItemMutation').body.variables.input.common;
  // Mercari's own draft form puts the label in shipping.classIds and suggestedClassIds
  // (draft-recording-4); a top-level shippingClassIds left the live item ship-on-your-own.
  assert.deepEqual(JSON.parse(JSON.stringify(common)), { name: 'Boys graphic tee', isAutoPriceDrop: false, description: 'Soft cotton tee',
    price: 1800, categoryId: 1934, conditionId: 3, sizeId: 168,
    shipping: { payerId: 1, classIds: [2508], suggestedClassIds: [2508], zipCode: '70115',
      dimensions: {}, weight: { weight: 7 } }, brandId: 5813,
    imageIds: ['i1', 'i2'] });
  assert.ok(!('shippingClassIds' in common));
  assert.equal(asked[0], 'masterQuery');
});

test('a Mercari draft that lost its label, payer, size or brand is refused, and never published', async () => {
  for (const [lost, named] of [[{ shippingPayer: { id: 2 }, shippingClasses: [] }, /shipping\).*ship-on-your-own with the seller paying/],
    [{ itemSize: null }, /\(size\)/], [{ brand: null }, /\(brand\)/], [{ itemCategory: { id: 1881 } }, /\(category\)/]]) {
    const saved = worker();
    saved.setDraft({ ...CLOTHING_DRAFT, ...lost });
    withVendooBrand(saved.c, saved.journal, [200, [{ value: '5813', label: 'Nike' }]]);
    const result = await saved.c.runMercariNative(message({ listing: clothing() }));
    assert.equal(result.ok, false);
    assert.match(result.error, named);
    const listed = worker({ respond: op => ({
      draftItemQuery: { draftItem: { ...CLOTHING_DRAFT, ...lost, draftItemStatus: 'DRAFT_ITEM_STATUS_READY', draftItemActionsRequired: [] } },
      listDraftItemMutation: { listDraftItem: 'm12345678901' },
    }[op]) });
    withVendooBrand(listed.c, listed.journal, [200, [{ value: '5813', label: 'Nike' }]]);
    const refused = await listed.c.runMercariNative(publish({ listing: clothing() }));
    assert.equal(refused.ok, false);
    assert.equal(refused.safe_to_retry, true);
    assert.match(refused.error, /does not match this revision/);
    assert.match(refused.error, named);
    assert.ok(!listed.opsOf().includes('listDraftItemMutation'));
  }
});

test('a Mercari parent category is never published, even with its id stored', async () => {
  const parent = clothing({ form: { overrides: { condition: '3', brand: 'Nike', categoryV2: { id: '1881', hasChildren: true } },
    specifics: { shipping: { payerId: 1, carrierId: 2508 } }, sizes: [] } });
  parent.direct_category_ids = { mercari: '1881' };
  const { c, opsOf } = worker();
  const result = await c.runMercariNative(publish({ listing: parent }));
  assert.equal(result.error_code, 'missing_fields');
  assert.ok(result.fields.some(entry => entry.field === 'categoryId'));
  assert.deepEqual(opsOf(), []);
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
  // Kids' XL (18/20) is Mercari's 2XL (18) by its numbers, not XL (14-16) by its letters.
  assert.deepEqual(size({ size: 'XL (18/20)' }), { id: 168 });
  assert.deepEqual(size({ size: 'XL (14/16)' }), { id: 105 });
  assert.deepEqual(size({ size: 'XL' }), { id: 105 });
  assert.deepEqual(size({ size: 'L (42-44)' }, MEN_TOP_SIZES), { id: 11 });
  assert.deepEqual(size({ size: '2XL' }), { id: 168 });
  assert.match(size({ size: 'XL' }, MEN_TOP_SIZES).problem, /XL \(46-48\) \(group 20\) or XL \(group 21\)/);
  assert.match(c.mercariSize({ size: 'XL', sizeType: 'Regular',
    form: { aspects: { '303_Size': '342' }, sizes: MEN_TOP_SIZES } }, 303).problem, /group 20/);
  assert.deepEqual(JSON.parse(JSON.stringify(c.mercariSize({
    form: { aspects: { '303_Size': '342' }, sizes: MEN_TOP_SIZES } }, 303))), { id: 342 });
  assert.deepEqual(JSON.parse(JSON.stringify(c.mercariSize({ size: 'XL (18/20)',
    form: { aspects: { '1934_Size': '105' }, sizes: BOYS_SIZES } }, 1934))), { id: 168 });
  assert.match(size({ size: 'XL', sizeType: 'Big & Tall' }, MEN_TOP_SIZES).problem, /XL \(46-48\) \(group 20\) or XL \(group 21\)/);
  const women = [
    { id: 4, name: 'M (8-10)', group: 11, group_name: "Women's" },
    { id: 293, name: 'M (8-10)', group: 12, group_name: 'Petite' },
    { id: 294, name: 'M (8-10)', group: 13, group_name: 'Plus' },
    { id: 295, name: 'M (8-10)', group: 14, group_name: 'Regular' },
    { id: 353, name: 'M (7-9)', group: 15, group_name: 'Juniors' },
  ];
  assert.deepEqual(size({ size: 'M (8-10)', sizeType: 'Petites', mercari_specifics: { size: 'M (8-10)' } }, women), { id: 293 });
  assert.deepEqual(size({ size: 'M (8-10)', sizeType: 'Petites', mercari_specifics: { size: 'M (8-10)' } },
    women.filter(row => row.group_name !== 'Regular')), { id: 293 });
  assert.deepEqual(size({ size: 'M (8-10)', mercari_specifics: { size: 'M (8-10)' } }, women), { id: 295 });
  assert.deepEqual(size({ size: 'M (8-10)', sizeType: 'Petites', mercari_specifics: { size: 'M (8-10)', sizeType: 'Regular' } }, women), { id: 295 });
  assert.deepEqual(size({ mercari_specifics: { size: 'M (8-10)', sizeType: 'Petite' } }, women), { id: 293 });
  assert.match(size({ mercari_specifics: { size: 'M (8-10)', sizeGroup: 'Not a group' } }, women).problem, /Women's/);
  assert.match(size({ mercari_specifics: { size: 'M (8-10)', sizeGroup: 'Not a group' } }, women).problem, /Petite/);
  assert.deepEqual(size({ size: 'L/XL' }), { problem: 'a size Mercari offers in this category' });
  // Mercari spells a letter size's numbers in its own label: a tag that reads 6 is its "S (4-6)".
  const tops = [
    { id: 2, name: 'XS (0-2)', group: 4, group_name: "Women's" },
    { id: 3, name: 'S (4-6)', group: 4, group_name: "Women's" },
    { id: 4, name: 'M (8-10)', group: 4, group_name: "Women's" },
    { id: 353, name: 'S (5-6)', group: 15, group_name: 'Juniors' },
  ];
  assert.deepEqual(size({ size: '6' }, tops), { id: 3 });
  assert.deepEqual(size({ size: '10' }, tops), { id: 4 });
  assert.deepEqual(size({ size: '6', sizeType: 'Juniors' }, tops), { id: 353 });
  assert.deepEqual(size({ size: '7' }, tops), { problem: 'a size Mercari offers in this category' });
  // A category that lists a size called 6 keeps its own 6 rather than the S (4-6) around it.
  assert.deepEqual(size({ size: '6' }, [{ id: 9, name: '6' }, { id: 3, name: 'S (4-6)' }]), { id: 9 });
  assert.deepEqual(JSON.parse(JSON.stringify(c.mercariSize({ size: 'M', form: { aspects: { '1934_Size': '103' }, sizes: BOYS_SIZES } }, 1934))), { id: 103 });
  assert.deepEqual(size({ size: 'M' }, []), { none: 'none' });
  assert.deepEqual(JSON.parse(JSON.stringify(c.mercariSize({ size: 'M', form: { aspects: {} } }, 1934))), { none: 'unknown' });
});

test('a letter saved where Mercari lists its own size resolves to that size, and never to another', () => {
  const { c } = worker();
  const size = (extra, sizes = BOYS_SIZES) => JSON.parse(JSON.stringify(c.mercariSize({ ...extra, form: { aspects: {}, sizes } }, 1934)));
  // The seller's plaid trousers: tag "S", Mercari only lists "S (6-7)".
  assert.deepEqual(size({ size: 'S', mercari_specifics: { size: 'S', nativeSizeId: 'S' } }), { id: 102 });
  // A size picked from the list is still exact.
  assert.deepEqual(size({ size: 'S', mercari_specifics: { size: 'S', nativeSizeId: '104' } }), { id: 104 });
  // A stale id that is not the item's own size is refused, never substituted.
  assert.match(size({ size: 'S', mercari_specifics: { size: 'S', nativeSizeId: 'M' } }).problem, /Mercari offers/);
  assert.match(size({ size: 'S', mercari_specifics: { size: 'S', nativeSizeId: 'nonsense' } }).problem, /Mercari offers/);
  // Clearing the row keeps the marketplaces' answer rather than guessing from the tag.
  assert.match(size({ size: 'S', mercari_specifics: { size: 'S', nativeSizeId: '' } }).problem, /Mercari offers/);
});

test('Mercari ships from current Settings instead of a cached Vendoo ZIP', () => {
  const { c } = worker();
  const listing = clothing();
  listing.form.ships_from_zip = '70125';
  assert.equal(c.mercariCommon(listing).shipping.zipCode, '70125');
});

test('Mercari schema errors retain the reason after a long input echo', () => {
  const c = loadWorker();
  const reason = 'Field "shippingClassIds" is not defined by type "DraftItemCommonInput".';
  const failure = c.mercariFailure('addDraftItemMutation', [{
    message: `Variable "$input" got invalid value { description: "${'shirt '.repeat(200)}" }; ${reason}`,
  }], true);
  assert.equal(failure.code, 'schema_drift');
  assert.equal(failure.marketplace_message, reason);
});

test('a sized Mercari listing whose size list could not be read stops before any upload', async () => {
  const unread = clothing();
  delete unread.form.sizes;
  const { c, requests } = worker();
  const result = await c.runMercariNative(message({ listing: unread }));
  assert.equal(result.error_code, 'missing_fields');
  assert.ok(result.fields.some(entry => entry.field === 'sizeId' && /could not read/.test(entry.message)));
  assert.equal(requests.length, 0);
});

test('a Mercari listing with no prepaid label stops before any upload rather than going ship-on-your-own', async () => {
  const { c, requests } = worker();
  const result = await c.runMercariNative(message({ listing: { ...listing, form: { specifics: { shipping: { payerId: 1 } } } } }));
  assert.equal(result.error_code, 'missing_fields');
  assert.ok(result.fields.some(entry => entry.field === 'shippingClassIds'));
  assert.equal(requests.length, 0);
});

test('a Mercari listing Mercari would refuse stops before any upload and names each field', async () => {
  for (const [listing, field] of [[clothing({ size: 'L/XL' }), 'sizeId'], [clothing({ price: 2400 }), 'price'], [clothing({ price: 0.4 }), 'price'],
    [clothing({ description: 'x'.repeat(1001) }), 'description'], [clothing({ weight_lb: 51 }), 'weight'],
    [clothing({ direct_category_ids: { mercari: '1885' }, form: { overrides: { condition: '3', categoryV2: { id: '1885', hasChildren: true } } } }), 'categoryId']]) {
    const { c, requests } = worker();
    const result = await c.runMercariNative(message({ listing }));
    assert.equal(result.error_code, 'missing_fields', field);
    assert.equal(result.safe_to_retry, true);
    assert.ok(result.fields.some(entry => entry.field === field), field);
    assert.equal(requests.length, 0, field);
  }
});

test('a Mercari brand catalog failure stops before any upload', async () => {
  const { c, requests, journal } = worker();
  withVendooBrand(c, journal, [503, {}]);
  const result = await c.runMercariNative(message({ listing: clothing() }));
  assert.equal(result.error_code, 'missing_fields');
  assert.equal(result.safe_to_retry, true);
  assert.deepEqual(JSON.parse(JSON.stringify(result.fields.map(entry => entry.field))), ['brandId']);
  assert.match(result.error, /could not read/);
  assert.equal(requests.length, 0);
});

test('Mercari lists with no brand when the seller clears it, and omits a name it does not list', async () => {
  const { c } = worker();
  const deadline = Date.now() + 30000;
  c.mercariCatalogData = async () => { throw new Error('should not read brands'); };
  assert.equal(await c.mercariBrandId({ brand: 'BRIGGS', mercari_specifics: {}, seller_fields: ['mercari_specifics.brand'] }, deadline), null);
  assert.equal(await c.mercariBrandId({ brand: 'BRIGGS', mercari_specifics: { brand: 'No Brand/Not sure' } }, deadline), null);
  assert.equal(await c.mercariBrandId({ brand: 'BRIGGS', mercari_specifics: { noBrand: true, brand: 'Nike' } }, deadline), null);
  c.mercariCatalogData = async () => ({ itemBrands: [{ id: 30138, name: 'GB Girls' }, { id: 5813, name: 'Nike' }] });
  assert.equal(await c.mercariBrandId({ brand: 'BRIGGS', mercari_specifics: { brand: 'GB girls' } }, deadline), 30138);
  assert.equal(await c.mercariBrandId({ brand: 'BRIGGS' }, deadline), null);
  const { c: run, requests, setDraft } = worker();
  setDraft({ ...CLOTHING_DRAFT, brand: { id: 0 } });
  run.mercariCatalogData = async () => ({ itemBrands: [{ id: 9, name: 'Nike SB' }] });
  const result = await run.runMercariNative(message({ listing: clothing({ brand: 'BRIGGS', mercari_specifics: { brand: '' }, seller_fields: ['mercari_specifics.brand'] }) }));
  assert.equal(result.ok, true, result.error);
  const common = requests.find(row => row.body.operationName === 'addDraftItemMutation').body.variables.input.common;
  assert.equal(common.brandId, 0);
  assert.equal(common.name, 'Boys graphic tee');
});

test('editing a Mercari draft clears a stale brand when the listing has no brand', async () => {
  const state = { draft: { ...CLOTHING_DRAFT, brand: { id: 5813, name: 'BRIGGS' } } };
  const { c, requests } = worker({
    respond(op, body) {
      if (op === 'uploadDraftItemImageMutation') return { uploadDraftItemImage: { images: [{ id: 'i1' }] } };
      if (op === 'editDraftItemMutation') {
        // Null or a missing brandId leaves the old brand. Zero is the clear Mercari accepts.
        if (body.variables.input.common.brandId === 0) state.draft = { ...CLOTHING_DRAFT, brand: null };
        return { editDraftItem: { id: 'm9AbC123' } };
      }
      if (op === 'draftItemQuery') return { draftItem: state.draft };
      return null;
    },
  });
  const result = await c.runMercariNative(message({
    draft_url: 'https://www.mercari.com/sell/draft/m9AbC123/',
    listing: clothing({ brand: 'BRIGGS', mercari_specifics: { noBrand: true } }),
  }));
  assert.equal(result.ok, true, result.error);
  const common = requests.find(row => row.body.operationName === 'editDraftItemMutation').body.variables.input.common;
  assert.equal(common.brandId, 0);
  assert.equal(requests.some(row => row.body.operationName === 'listDraftItemMutation'), false);
});

test('a Mercari draft input it no longer accepts is reported as a form change, safe to retry', async () => {
  const { c, journal } = worker({ respond: op => (op === 'addDraftItemMutation'
    ? { errors: [{ message: 'Field "sizeId" is not defined by type "DraftItemCommonInput".', extensions: { code: 'GRAPHQL_VALIDATION_FAILED' } }] }
    : { uploadDraftItemImage: { images: [{ id: 'i1' }] } }) });
  withVendooBrand(c, journal, [200, [{ value: '5813', label: 'Nike' }]]);
  const result = await c.runMercariNative(message({ listing: clothing() }));
  assert.equal(result.error_code, 'schema_drift');
  assert.equal(result.safe_to_retry, true);
  assert.match(result.marketplace_message, /sizeId/);
});

const NATIVE_MASTER = {
  itemCategories: [{ id: 3, name: 'Kids', parentId: 0, itemSizeGroupId: 0 },
    { id: 1877, name: 'Girls tops & t-shirts', parentId: 3, itemSizeGroupId: 0 },
    { id: 1911, name: 'Girls (4+)', parentId: 1877, itemSizeGroupId: 8 }],
  itemSizeGroups: [{ id: 8, sizeIds: [105, 168], subGroupIds: [83] }],
  itemSizeSubGroups: [{ id: 83, title: '4Y+', sizeIds: [105, 168] }],
  itemSizes: [{ id: 105, title: 'XL', subtitle: '(14-16)', itemSizeSubGroupId: 83 },
    { id: 168, title: '2XL', subtitle: '(18)', itemSizeSubGroupId: 83 }],
  itemBrands: [{ id: 30138, name: 'GB Girls' }],
};

test('Mercari Smart Pricing sends its floor and refuses mismatched read-back or an invalid floor', () => {
  const c = loadWorker();
  const item = { ...listing, mercari_specifics: { smartPricing: { enabled: true, minimumPrice: 18 } } };
  const common = c.mercariCommon(item);
  assert.equal(common.isAutoPriceDrop, true);
  assert.equal(common.minPriceForAutoPriceDrop, 1800);
  const draft = { name: common.name, description: common.description, price: common.price, categoryId: common.categoryId,
    conditionId: common.conditionId, payerId: 1, classIds: [2509], isAutoPriceDrop: true, minPriceForAutoPriceDrop: 1800 };
  assert.deepEqual(Array.from(c.mercariDraftDifferences(draft, common)), []);
  assert.ok(c.mercariDraftDifferences({ ...draft, isAutoPriceDrop: false }, common).includes('Smart Pricing'));
  assert.ok(c.mercariDraftDifferences({ ...draft, conditionId: 1 }, common).includes('condition'));
  assert.ok(c.mercariMissing(item, { ...common, minPriceForAutoPriceDrop: common.price }).some(field => field.field === 'minPriceForAutoPriceDrop'));
});

test('Mercari reads its own catalog over GraphQL without Vendoo or session export', async () => {
  const { c, requests, journal } = worker({ respond: op => op === 'masterQuery' ? { master: NATIVE_MASTER } : undefined });
  c.loadCachedVendooSession = () => assert.fail('Direct listing must not read Vendoo sign-in');
  const master = await c.mercariCatalogData(Date.now() + 30000);
  assert.equal(master.itemBrands[0].name, 'GB Girls');
  assert.match(requests[0].body.query, /itemSizeGroups/);
  assert.equal(await c.mercariBrandId({ brand: 'GB girls', form: { overrides: { brand: '999' } } }, Date.now() + 30000), 30138);
  assert.ok(!JSON.stringify(journal).includes('private-session'));
  const prepared = c.mercariCatalogListing({ size: 'XL (18/20)', direct_category_ids: { mercari: '1911' },
    form: { sizes: [{ id: 999, name: 'XL (18/20)' }] } }, master);
  assert.equal(c.mercariSize(prepared, 1911).id, 168);
  assert.equal(prepared.form.sizes[0].name, 'XL (14-16)');
  assert.throws(() => c.mercariCatalogListing({ direct_category_ids: { mercari: '999999' } }, master), /current list/);
  // Mercari's picker offers a category with subcategories ("All Girls tops & t-shirts"), but not a top-level one.
  const parent = c.mercariCatalogListing({ direct_category_ids: { mercari: '1877' } }, master);
  assert.ok(!c.mercariMissing(parent, c.mercariCommon(parent)).some(row => row.field === 'categoryId'));
  const top = c.mercariCatalogListing({ direct_category_ids: { mercari: '3' } }, master);
  assert.ok(c.mercariMissing(top, c.mercariCommon(top)).some(row => row.field === 'categoryId' && /Girls tops/.test(row.needs)));
});

test('native Mercari preparation replaces stale sizes and labels before uploading and verifies its result', async () => {
  const { c, requests, setDraft } = worker({ prepareCatalog: true });
  c.mercariCatalogData = async () => NATIVE_MASTER;
  c.mercariShippingClasses = async () => [{ id: 2550, carrierDisplayName: 'USPS Ground Advantage', fee: 599, name: '0.5 lb' }];
  setDraft({ ...CLOTHING_DRAFT, name: 'Girls tee', itemCategory: { id: 1911 }, brand: { id: 30138 },
    shippingClasses: [{ id: 2550 }], shippingPayer: { id: 2 } });
  const result = await c.runMercariNative(message({ listing: { ...clothing(), title: 'Girls tee', brand: 'GB Girls',
    direct_category_ids: { mercari: '1911' }, mercari_specifics: { shippingPayerId: 2 } } }));
  assert.equal(result.state, 'draft');
  const common = requests.find(row => row.body.operationName === 'addDraftItemMutation').body.variables.input.common;
  assert.equal(common.sizeId, 168);
  assert.equal(common.brandId, 30138);
  assert.deepEqual(Array.from(common.shipping.classIds), [2550]);
  assert.equal(common.shipping.payerId, 2);
  assert.equal(c.mercariShippingListing({ mercari_specifics: { shippingClassId: '2508' } }, [{ id: 2550 }]).form.specifics.shipping.carrierId, null);
});

test('a saved shipping-label default follows its carrier across package weights', () => {
  const c = loadWorker();
  const classes = [
    { id: 2551, carrierDisplayName: 'USPS Ground Advantage', name: '2 lb' },
    { id: 2600, carrierDisplayName: 'UPS Ground', name: '2 lb' },
  ];
  // The saved id belonged to another weight tier, so the label picks this package's class.
  const saved = c.mercariShippingListing({ mercari_specifics: {},
    form: { mercari_shipping_label: { id: '2508', label: 'UPS Ground' } } }, classes);
  assert.equal(saved.form.specifics.shipping.carrierId, 2600);
  // No saved default keeps the seller's usual USPS Ground Advantage.
  const automatic = c.mercariShippingListing({ mercari_specifics: {} }, classes);
  assert.equal(automatic.form.specifics.shipping.carrierId, 2551);
  // A choice made on this listing still wins over the saved default.
  const explicit = c.mercariShippingListing({ mercari_specifics: { shippingClassId: '2551' },
    form: { mercari_shipping_label: { id: '2508', label: 'UPS Ground' } } }, classes);
  assert.equal(explicit.form.specifics.shipping.carrierId, 2551);
});
