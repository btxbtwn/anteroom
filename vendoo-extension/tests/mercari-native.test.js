const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

const listing = { title: 'Test jacket', description: 'Private draft', price: 24.6, condition: 'Pre-Owned - Good',
  marketplace_category_ids: { mercari: '303' }, zipCode: '70125', weight_lb: 0, weight_oz: 10 };
const HEADERS = { authorization: 'Bearer private-session', 'x-csrf-token': 'private-csrf', 'x-platform': 'web', 'x-app-version': '3.106.6' };

function worker({ session = { headers: HEADERS }, respond } = {}) {
  const c = loadWorker();
  c.AbortSignal = AbortSignal;
  c.FormData = FormData;
  c.Blob = Blob;
  const journal = {};
  c.chrome.storage = {
    session: { get: async () => (session ? { mercari_session: session } : {}) },
    local: { get: async key => ({ [key]: journal[key] }), set: async values => Object.assign(journal, values) },
  };
  c.chrome.declarativeNetRequest = { updateSessionRules: async () => {} };
  c.chrome.tabs = { create: () => { throw new Error('opened a tab'); }, query: () => { throw new Error('read tabs'); } };
  c.nativePhoto = async () => ({ base64: 'AA==', mime_type: 'image/jpeg' });
  c.mercariPhotoBlob = async () => new Blob(['jpeg'], { type: 'image/jpeg' });
  const requests = [];
  let draft = { name: 'Test jacket', description: 'Private draft', price: 2500, itemCondition: { id: 3 }, photos: [{ id: 'i1' }, { id: 'i2' }] };
  c.fetch = async (url, options) => {
    const body = options.body instanceof FormData ? { multipart: options.body } : JSON.parse(options.body);
    requests.push({ url, headers: options.headers, credentials: options.credentials, body });
    const op = body.multipart ? JSON.parse(body.multipart.get('operations')).operationName : body.operationName;
    const data = respond ? respond(op, body) : {
      uploadDraftItemImageMutation: { uploadDraftItemImage: { images: [{ id: 'i1' }, { id: 'i2' }] } },
      addDraftItemMutation: { addDraftItem: { item: { id: 'm9AbC123', draftItemStatus: 'action_required' } } },
      editDraftItemMutation: { editDraftItem: { id: 'm9AbC123' } },
      draftItemQuery: { draftItem: draft },
    }[op];
    return { ok: true, status: 200, json: async () => (data && data.errors ? data : { data }) };
  };
  return { c, requests, journal, setDraft: value => { draft = value; } };
}

const message = extra => ({ job_id: 'job-1', payload: { marketplace: 'mercari', action: 'save_draft', operation_id: 'op-1',
  listing, photos: [{ id: 'p1', mime_type: 'image/jpeg' }, { id: 'p2', mime_type: 'image/jpeg' }], ...extra } });

test('a new Mercari draft uploads photos, saves once, and is confirmed by reading it back, with no tab', async () => {
  const { c, requests, journal } = worker();
  const result = await c.runMercariNative(message());
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, state: 'draft', draft_url: 'https://www.mercari.com/sell/draft/m9AbC123/' });
  const ops = requests.map(r => (r.body.multipart ? JSON.parse(r.body.multipart.get('operations')).operationName : r.body.operationName));
  assert.deepEqual(ops, ['uploadDraftItemImageMutation', 'addDraftItemMutation', 'draftItemQuery']);
  const upload = requests[0].body.multipart;
  assert.deepEqual(JSON.parse(upload.get('map')), { 0: ['variables.input.photos.0'], 1: ['variables.input.photos.1'] });
  assert.deepEqual(JSON.parse(upload.get('operations')).variables, { input: { photos: [null, null] } });
  assert.deepEqual(requests[1].body.variables, { input: { common: { name: 'Test jacket', isAutoPriceDrop: false, description: 'Private draft', price: 2500, categoryId: 303, conditionId: 3, shipping: { payerId: 1, zipCode: '70125', dimensions: {}, weight: { weight: 10 } }, imageIds: ['i1', 'i2'] } } });
  assert.equal(requests[1].body.extensions.persistedQuery.sha256Hash, '2b7de5aa72261ef11ab70a37cd06eb470bf6c57097d3e9f346f57025c4fc9a97');
  assert.deepEqual(requests[2].body.variables, { input: { draftItemId: 'm9AbC123' } });
  assert.ok(requests.every(r => r.url === 'https://www.mercari.com/v1/api' && r.headers['x-csrf-token'] === 'private-csrf'));
  assert.ok(requests.every(r => !('x-socure-device-token' in r.headers)));
  assert.equal(journal['native_operation_op-1'].receipt_url, 'https://www.mercari.com/sell/draft/m9AbC123/');
  assert.ok(!JSON.stringify(result).includes('private'));
});

test('an existing Mercari draft is edited in place without re-uploading photos', async () => {
  const { c, requests } = worker();
  const result = await c.runMercariNative(message({ draft_url: 'https://www.mercari.com/sell/draft/m9AbC123/' }));
  assert.equal(result.ok, true);
  assert.deepEqual(requests.map(r => r.body.operationName), ['editDraftItemMutation', 'draftItemQuery']);
  assert.deepEqual(requests[0].body.variables, { input: { id: 'm9AbC123', common: { name: 'Test jacket', isAutoPriceDrop: false, description: 'Private draft', price: 2500, categoryId: 303, conditionId: 3, shipping: { payerId: 1, zipCode: '70125', dimensions: {}, weight: { weight: 10 } } } } });
});

test('Mercari asks for a reconnect when no session was captured, before any request', async () => {
  const { c, requests } = worker({ session: null });
  const result = await c.runMercariNative(message());
  assert.match(result.error, /Reconnect Mercari/);
  assert.equal(result.safe_to_retry, true);
  assert.equal(requests.length, 0);
});

test('a lost reply after the draft save is never retried', async () => {
  const { c, requests } = worker();
  const original = c.fetch;
  c.fetch = async (url, options) => {
    if (!(options.body instanceof FormData) && JSON.parse(options.body).operationName === 'addDraftItemMutation') {
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
});

test('a changed Mercari form is reported as needing an update', async () => {
  const { c } = worker({ respond: () => ({ errors: [{ message: 'PersistedQueryNotFound' }] }) });
  const result = await c.runMercariNative(message());
  assert.match(result.error, /changed its listing form/);
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
  assert.equal(result.safe_to_retry, true);
  assert.equal(requests.length, 0);
});

const DRAFT_URL = 'https://www.mercari.com/sell/draft/m9AbC123/';
const publish = extra => message({ action: 'publish', confirm: true, draft_url: DRAFT_URL, ...extra });

test('a READY Mercari draft is listed with one request, the way its drafts page does, with no tab', async () => {
  const { c, requests, journal } = worker({ respond: op => ({
    draftItemQuery: { draftItem: { name: 'Test jacket', description: 'Private draft', price: 2500, itemCondition: { id: 3 },
      photos: [{ id: 'i1' }], draftItemStatus: 'DRAFT_ITEM_STATUS_READY', draftItemActionsRequired: [] } },
    listDraftItemMutation: { listDraftItem: 'm12345678901' },
  }[op]) });
  const result = await c.runMercariNative(publish());
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, state: 'published', draft_url: DRAFT_URL,
    listing_url: 'https://www.mercari.com/us/item/m12345678901/' });
  assert.deepEqual(requests.map(r => r.body.operationName), ['draftItemQuery', 'listDraftItemMutation']);
  assert.deepEqual(requests[1].body.variables, { input: { draftItemId: 'm9AbC123' } });
  assert.equal(journal['native_operation_op-1'].listing_url, 'https://www.mercari.com/us/item/m12345678901/');
});

test('publishing an incomplete Mercari draft says what Mercari still needs, without listing it', async () => {
  const { c, requests } = worker({ respond: op => ({
    draftItemQuery: { draftItem: { name: 'Test jacket', description: 'Private draft', price: 2500, photos: [{ id: 'i1' }],
      draftItemStatus: 'DRAFT_ITEM_STATUS_ACTION_REQUIRED', draftItemActionsRequired: [{ field: 'brandId' }, { field: 'shippingClassIds' }] } },
  }[op]) });
  const result = await c.runMercariNative(publish());
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, true);
  assert.match(result.error, /brand, shipping/);
  assert.deepEqual(requests.map(r => r.body.operationName), ['draftItemQuery']);
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
