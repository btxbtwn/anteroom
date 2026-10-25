const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');
const id = 'abcdef123456abcdef123456';
const url = `https://poshmark.com/edit-listing/${id}`;

function setup() {
  const c = loadWorker();
  Object.assign(c, { FormData, Blob, AbortSignal, atob });
  c.chrome.tabs = new Proxy({}, { get: () => () => assert.fail('No tabs during listing') });
  c.chrome.scripting = { executeScript: () => assert.fail('No page execution during listing') };
  let post = { id, status: 'user_draft', title: 'Test', description: 'Private', price_amount: { val: '12', currency_code: 'USD' },
    cover_shot: { id }, pictures: [], catalog: { category: 'shirt' }, inventory: { size_quantities: [{ size_id: 'M' }] }, condition: 'new' };
  const requests = [];
  c.fetch = async (path, options) => {
    requests.push({ path, ...options });
    if (options.method !== 'GET' && !path.includes('/status/published')) post = { ...post, ...JSON.parse(options.body).post };
    if (path.includes('/status/published')) post.status = 'published';
    return { ok: true, json: async () => ({ data: post }) };
  };
  const msg = { payload: { marketplace: 'poshmark', action: 'save_draft', draft_url: url,
    listing: { title: 'Test', description: 'Private', price: 12 }, operation_id: 'abc123' } };
  return { c, msg, requests };
}

test('Poshmark saves and verifies an existing private draft with no tabs or scripts', async () => {
  const { c, msg, requests } = setup();
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.state, 'draft');
  assert.deepEqual(requests.map(request => request.method), ['GET', 'POST', 'PUT', 'GET']);
  assert.ok(requests.every(request => !request.path.includes('/status/published')));
  assert.equal(JSON.parse(requests[1].body).post.catalog.category, 'shirt');
});

test('Poshmark publication requires confirmation and makes one publish request', async () => {
  const { c, msg, requests } = setup();
  msg.payload.action = 'publish';
  assert.equal((await c.runPoshmarkApi(msg)).ok, false);
  assert.equal(requests.length, 0);
  msg.payload.confirm = true;
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.state, 'published');
  assert.deepEqual(requests.map(request => request.method), ['GET', 'PUT', 'GET']);
});

test('Poshmark never repeats a write after a lost reply', async () => {
  const { c, msg, requests } = setup();
  const fetch = c.fetch;
  c.fetch = async (path, options) => {
    if (options.method !== 'GET') { requests.push(options); throw new Error('Disconnected'); }
    return fetch(path, options);
  };
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.safe_to_retry, false);
  assert.deepEqual(requests.map(request => request.method), ['GET', 'POST']);
});

test('Poshmark creates a private post and attaches every photo entirely in the worker', async () => {
  const { c, msg } = setup();
  c.chrome.cookies = { get: async () => ({ value: `e30.${btoa(JSON.stringify({ user_id: id }))}.signature` }) };
  c.nativePhoto = async () => ({ base64: btoa('photo'), name: 'test.jpg', mime_type: 'image/jpeg' });
  let post = { id, status: 'draft', pictures: [] };
  let creations = 0;
  let photos = 0;
  const writes = [];
  c.fetch = async (path, options) => {
    if (options.method !== 'GET') writes.push(path);
    if (/\/users\/[^/]+\/posts\?/.test(path)) creations++;
    else if (path.includes('/media/scratch')) {
      photos++;
      return { ok: true, json: async () => ({ id: String(photos).padStart(24, '0') }) };
    } else if (options.method !== 'GET') post = { ...post, ...JSON.parse(options.body).post, status: 'user_draft' };
    return { ok: true, json: async () => ({ data: post }) };
  };
  msg.payload.draft_url = null;
  msg.payload.photos = [{ id: 'a1' }, { id: 'a2' }];
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.state, 'draft');
  assert.equal(creations, 1);
  assert.equal(photos, 2);
  assert.equal(post.pictures.length, 1);
  assert.ok(writes.every(path => !path.includes('/status/published')));
});
