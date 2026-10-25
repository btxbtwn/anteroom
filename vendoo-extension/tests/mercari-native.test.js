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
