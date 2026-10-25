const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call, reply } = require('./worker-context');
const DRAFT = 'https://www.depop.com/sellinghub/drafts/edit/123456/';
const listing = { title: 'Test', description: 'Private draft', price: 12 };

function page() {
  const c = loadWorker();
  c.AbortSignal = AbortSignal;
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  const requests = [];
  let saved = { id: 123456, description: 'Test\n\nPrivate draft', price_amount: '12', price_currency: 'USD',
    pictures: [{ id: 789 }], missing_fields: ['brand'], shipping_methods: [] };
  c.fetch = async (url, options) => {
    requests.push({ url, ...options });
    if (options.method === 'PUT') saved = { ...saved, ...JSON.parse(options.body), pictures: JSON.parse(options.body).pictures.map(id => ({ id })) };
    return reply(200, saved);
  };
  return { c, requests, payload: { action: 'save_draft', draft_url: DRAFT, listing, deadline: Date.now() + 30000 } };
}

test('Depop accepts only durable native edit links', () => {
  const c = loadWorker();
  assert.equal(call(c, 'nativeDraftUrl', 'depop', DRAFT), DRAFT);
  for (const url of ['https://www.depop.com/products/test/', 'https://www.depop.com.evil/sellinghub/drafts/edit/123/',
    'https://www.depop.com/sellinghub/drafts/edit/bad.id/']) assert.equal(call(c, 'nativeDraftUrl', 'depop', url), '');
  assert.equal(call(c, 'nativeDraftUrl', 'depop', DRAFT.replace('123456', 'draft-opaque-id')), DRAFT.replace('123456', 'draft-opaque-id'));
});

test('Depop draft writes the same colour, condition, and option codes Vendoo would', async () => {
  const { c, requests, payload } = page();
  payload.listing = { ...listing, primaryColor: 'Black', secondaryColor: 'Gray', condition: 'Pre-Owned - Good',
    form: { overrides: { condition: 'Pre-Owned - Good' }, specifics: { style: ['casual', 'retro'], age: ['modern'], source: [] } } };
  await c.depopApiAction('save', payload);
  const body = JSON.parse(requests[1].body);
  assert.deepEqual(body.colour, ['black', 'grey']);
  assert.equal(body.condition, 'used_good');
  assert.deepEqual(body.style, ['casual', 'retro']);
  assert.deepEqual(body.age, ['modern']);
  assert.equal(body.source, undefined);
});

test('Depop keeps its own condition slugs and maps Studio labels the way Vendoo does', () => {
  const c = loadWorker();
  for (const slug of ['brand_new', 'used_like_new', 'used_excellent', 'used_good', 'used_fair']) assert.equal(c.depopCondition(slug), slug);
  const labels = { 'New With Tags/Box': 'brand_new', 'New Without Tags/Box': 'used_like_new', 'New With Imperfections': 'used_good',
    'Pre-Owned - Excellent': 'used_excellent', 'Pre-Owned - Good': 'used_good', 'Pre-Owned - Fair': 'used_fair',
    'Poor (Major flaws)': 'used_fair', 'Like New': 'used_like_new', New: 'brand_new', '': '' };
  for (const [label, slug] of Object.entries(labels)) assert.equal(c.depopCondition(label), slug, label);
});

test('Depop draft sends the slug Studio already chose', async () => {
  const { c, requests, payload } = page();
  payload.listing = { ...listing, condition: 'New Without Tags/Box', form: { overrides: { condition: 'used_like_new' }, specifics: {} } };
  await c.depopApiAction('save', payload);
  assert.equal(JSON.parse(requests[1].body).condition, 'used_like_new');
});

test('Depop draft updates preserve photos and never call the publish endpoint', async () => {
  const { c, requests, payload } = page();
  const result = await c.depopApiAction('save', payload);
  assert.equal(result.photo_count, 1);
  assert.deepEqual(requests.map(r => r.method), ['GET', 'PUT', 'GET']);
  assert.equal(JSON.parse(requests[1].body).pictures[0], 789);
  assert.ok(requests.every(r => !r.url.includes('/drafts/products/')));
  assert.ok(!JSON.stringify(result).includes('private-session'));
});

test('Depop publishes only an explicitly confirmed complete draft and exactly one ID', async () => {
  const { c, requests, payload } = page();
  assert.ok((await c.depopApiAction('publish', payload)).error);
  assert.equal(requests.length, 0);
  const approved = { ...payload, action: 'publish', confirm: true };
  assert.ok((await c.depopApiAction('publish', approved)).error);
  assert.deepEqual(requests.map(r => r.method), ['GET']);
  requests.length = 0;
  c.fetch = async (url, options) => {
    requests.push({ url, ...options });
    return reply(200, options.method === 'GET'
      ? { description: 'Test\n\nPrivate draft', price_amount: '12', pictures: [{}], missing_fields: [] } : { id: 'job-1' });
  };
  const result = await c.depopApiAction('publish', approved);
  assert.equal(result.publish_job_id, 'job-1');
  assert.deepEqual(requests.map(r => r.method), ['GET', 'POST']);
  assert.deepEqual(JSON.parse(requests[1].body), { draft_ids: ['123456'] });
});

test('an uncertain Depop mutation is never retried', async () => {
  const { c, requests, payload } = page();
  c.fetch = async (_url, options) => { requests.push(options); throw new Error('Disconnected'); };
  const result = await c.depopApiAction('create', payload);
  assert.equal(result.safe_to_retry, false);
  assert.equal(requests.length, 1);
});

test('Depop refusals are classified, carry Depop\'s own words, and never export a token', async () => {
  for (const [body, code, own] of [
    ['{"error":"captcha_required","token":"private-response-token"}', 'challenge', 'captcha_required'],
    ['{"error":"invalid_token","token":"private-response-token"}', 'signed_out', 'invalid_token'],
    ['{"error":"Forbidden","token":"private-response-token"}', 'signed_out', 'Forbidden'],
  ]) {
    const { c, requests, payload } = page();
    c.fetch = async (_url, options) => {
      requests.push(options);
      return reply(403, JSON.parse(body));
    };
    const result = await c.depopApiAction('create', payload);
    assert.equal(result.error_code, code);
    assert.equal(result.marketplace_message, own);
    // A 403 is a refusal: nothing was written.
    assert.equal(result.safe_to_retry, true);
    assert.equal(requests.length, 1);
    assert.ok(!JSON.stringify(result).includes('private-response-token'));
  }
});

function newDraft({ photoAnswer } = {}) {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  c.chrome.storage.local.get = async () => ({});
  c.nativePhoto = async () => ({ base64: 'AA==', mime_type: 'image/jpeg' });
  c.createImageBitmap = async () => ({ width: 10, height: 10, close() {} });
  c.OffscreenCanvas = function OffscreenCanvas() {
    this.getContext = () => ({ fillRect() {}, drawImage() {} });
    this.convertToBlob = async () => new Blob(['jpeg']);
  };
  const requests = [];
  c.fetch = async (url, options = {}) => {
    const { hostname, pathname } = new URL(url);
    requests.push({ method: options.method, hostname, pathname, body: options.body });
    if (hostname.endsWith('amazonaws.com')) return photoAnswer ? photoAnswer(requests.filter(r => r.method === 'PUT').length) : reply(200, '');
    if (options.method === 'POST' && pathname === '/presentation/api/v1/drafts/') return reply(200, { id: 555 });
    if (options.method === 'POST' && pathname === '/presentation/api/v1/pictures/') {
      return reply(200, { id: 9, url: 'https://media-photos-depop.s3-accelerate.amazonaws.com/p/9.jpg?X-Amz-Signature=private' });
    }
    return reply(200, { id: 555, description: 'Test\n\nPrivate draft', price_amount: '12', pictures: [] });
  };
  const msg = { job_id: 'job-1', payload: { action: 'save_draft', operation_id: 'abc123', listing,
    photos: [{ id: 'p1', mime_type: 'image/jpeg' }] } };
  return { c, requests, msg };
}

test('a Depop photo failure after the draft exists returns its link and is safe to retry', async () => {
  const { c, requests, msg } = newDraft({ photoAnswer: () => reply(403, '<Error>AccessDenied</Error>', { 'content-type': 'application/xml' }) });
  const result = await c.runDepopNative(msg);
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.draft_url, 'https://www.depop.com/sellinghub/drafts/edit/555/');
  assert.ok(!requests.some(r => r.method === 'PUT' && r.hostname === 'webapi.depop.com'));
  assert.ok(!JSON.stringify(result).includes('private'));
});

test('a transient Depop photo upload is retried', async () => {
  const { c, requests, msg } = newDraft({ photoAnswer: n => (n === 1 ? reply(503, 'Slow Down') : reply(200, '')) });
  await c.runDepopNative(msg);
  assert.equal(requests.filter(r => r.hostname.endsWith('amazonaws.com')).length, 2);
});

test('Depop photo uploads go only to Depop\'s S3 hosts', async () => {
  const { c, requests, msg } = newDraft();
  const original = c.fetch;
  c.fetch = async (url, options = {}) => (new URL(url).pathname === '/presentation/api/v1/pictures/'
    ? reply(200, { id: 9, url: 'https://evil.example.com/p/9.jpg' }) : original(url, options));
  const result = await c.runDepopNative(msg);
  assert.equal(result.error_code, 'schema_drift');
  assert.ok(!requests.some(r => r.hostname === 'evil.example.com'));
});

test('a Depop description is capped at 1000 characters and read back against the capped text', async () => {
  const { c, requests, payload } = page();
  payload.listing = { ...listing, description: 'x'.repeat(1500) };
  const capped = `Test\n\n${'x'.repeat(1500)}`.slice(0, 1000);
  const original = c.fetch;
  c.fetch = async (url, options) => {
    const answer = await original(url, options);
    if (options.method === 'PUT') assert.equal(JSON.parse(options.body).description.length, 1000);
    return answer;
  };
  const result = await c.depopApiAction('save', payload);
  assert.equal(result.description, capped);
  assert.equal(requests.length, 3);
});

test('Depop updates and reads an existing private draft without any tab or page execution', async () => {
  const { c, payload } = page();
  c.chrome.tabs = new Proxy({}, { get: () => () => { assert.fail('No marketplace tabs'); } });
  c.chrome.scripting = { executeScript: () => { assert.fail('No page execution'); } };
  c.chrome.storage.local.get = async () => ({});
  const result = await c.runDepopNative({ payload: { ...payload, operation_id: 'abc123' } });
  assert.equal(result.ok, true);
  assert.equal(result.state, 'draft');
});

test('Depop\'s own missing fields come back by name, safe to retry, with the draft link', async () => {
  const { c, requests, payload } = page();
  c.chrome.storage.local.get = async () => ({});
  c.fetch = async (url, options) => {
    requests.push({ url, ...options });
    return reply(200, { description: 'Test\n\nPrivate draft', price_amount: '12', pictures: [{}], missing_fields: ['brand', 'parcel_size'] });
  };
  const result = await c.runDepopNative({ payload: { ...payload, action: 'publish', confirm: true, operation_id: 'abc123' } });
  assert.equal(result.ok, false);
  assert.equal(result.error_code, 'missing_fields');
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.draft_url, DRAFT);
  assert.deepEqual(JSON.parse(JSON.stringify(result.fields)), [{ field: 'brand', message: 'Depop needs a brand' },
    { field: 'parcel_size', message: 'Depop needs a parcel size' }]);
  assert.equal(result.marketplace_message, 'missing_fields: brand, parcel_size');
  assert.equal(result.error, 'Depop needs a brand, a parcel size.');
  assert.ok(requests.every(r => (r.method || 'GET') === 'GET'));
});
