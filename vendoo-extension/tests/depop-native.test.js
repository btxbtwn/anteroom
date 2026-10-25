const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call } = require('./worker-context');
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
    return { ok: true, status: 200, json: async () => saved };
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
    return { ok: true, status: 200, json: async () => options.method === 'GET'
      ? { description: 'Test\n\nPrivate draft', price_amount: '12', pictures: [{}], missing_fields: [] } : { id: 'job-1' } };
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

test('Depop reports verification separately from an unexplained 403 without exporting the response', async () => {
  for (const [body, expected] of [
    ['{"error":"captcha_required","token":"private-response-token"}', 'browser verification'],
    ['{"error":"invalid_token","token":"private-response-token"}', 'invalid or expired session'],
    ['{"error":"Forbidden","token":"private-response-token"}', 'did not provide a recognized reason'],
  ]) {
    const { c, requests, payload } = page();
    c.fetch = async (_url, options) => {
      requests.push(options);
      return { ok: false, status: 403, text: async () => body };
    };
    const result = await c.depopApiAction('create', payload);
    assert.match(result.error, new RegExp(expected));
    assert.equal(result.safe_to_retry, false);
    assert.equal(requests.length, 1);
    assert.ok(!JSON.stringify(result).includes('private-response-token'));
  }
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
