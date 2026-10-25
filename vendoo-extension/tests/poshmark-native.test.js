const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker, reply: response } = require('./worker-context');

const USER = 'a'.repeat(24);
const POST = 'b'.repeat(24);
const DEPT = '01008c10d97b4e1245005764';
const SHIRTS = '07008c10d97b4e1245005764';
const TEES = '33009813d97b4e3995005764';
const size = (id, display = id) => ({ id, short: id, display, display_with_size_set: display, size_system: 'us' });
const CATALOG = { data: {
  colors: [{ name: 'White' }, { name: 'Gray' }, { name: 'Blue' }],
  catalog: { departments: [{ id: DEPT, display: 'Men', categories: [{ id: SHIRTS, display: 'Shirts',
    category_features: [{ id: TEES, display: 'Tees - Short Sleeve' }],
    size_sets: [{ name: 'Standard', tags: ['standard'], size_systems: { us: { sizes: [size('M'), size('XL')] } } },
      { name: 'Big & Tall', tags: ['big_&_tall'], size_systems: { us: { sizes: [size('XXL'), size('3XL')] } } }] }] }] },
} };
const LISTING = { title: 'Vintage tee', description: 'Soft cotton tee', price: 18, brand: 'Hanes', size: 'M', condition: 'Pre-Owned - Good',
  primaryColor: 'White', secondaryColor: 'Grey', marketplace_category_objects: { poshmark: { path: [DEPT, SHIRTS, TEES] } },
  form: { specifics: { originalPrice: 55 } },
  poshmark_specifics: { originalPrice: 30, styleTags: ['Casual', 'Retro', 'Streetwear', 'Extra'], smartSellMinimumPrice: 12 } };

const PAGE = `<html><script>window.__INITIAL_STATE__={"csrftoken":"page-csrf","userCookies":{"userInfo":{"uid":"${USER}"}}};</script></html>`;
const page = () => response(200, PAGE, { 'content-type': 'text/html' });

function setup({ publishAnswer = [200, {}], status = 'published' } = {}) {
  const c = loadWorker();
  Object.assign(c, { FormData, Blob, AbortSignal, atob });
  c.chrome.tabs = new Proxy({}, { get: () => () => assert.fail('No tabs during listing') });
  c.chrome.scripting = { executeScript: () => assert.fail('No page execution during listing') };
  c.chrome.cookies = { get: async () => ({ value: `x.${Buffer.from(JSON.stringify({ user_id: USER })).toString('base64')}.y` }) };
  const journal = {};
  c.chrome.storage.local.get = async key => ({ [key]: journal[key] });
  c.chrome.storage.local.set = async values => Object.assign(journal, values);
  c.nativePhoto = async () => ({ base64: 'AA==', mime_type: 'image/jpeg', name: 'p.jpg' });
  c.poshmarkCatalog = null;
  const requests = [];
  let photo = 0;
  c.fetch = async (url, options = {}) => {
    const path = new URL(url).pathname;
    const method = options.method || 'GET';
    requests.push({ method, path, headers: options.headers, body: typeof options.body === 'string' ? JSON.parse(options.body) : options.body });
    const reply = response;
    if (path === '/create-listing') return page();
    if (path === '/vm-rest/meta/catalog_v2') return reply(200, CATALOG);
    if (method === 'POST' && path === `/vm-rest/users/${USER}/posts`) return reply(200, { data: { id: POST, status: 'draft' } });
    if (path.endsWith('/media/scratch')) return reply(200, { data: { id: `${'c'.repeat(23)}${photo++}` } });
    if (method === 'PUT' && path.endsWith('/status/published')) return reply(...publishAnswer);
    if (method === 'GET' && path === `/vm-rest/posts/${POST}`) return reply(200, { data: { id: POST, status } });
    return reply(200, { data: {} });
  };
  const msg = { job_id: 'job-1', payload: { marketplace: 'poshmark', action: 'list', confirm: true, operation_id: 'op-1',
    listing: LISTING, photos: [{ id: 'p1', mime_type: 'image/jpeg' }, { id: 'p2', mime_type: 'image/jpeg' }] } };
  return { c, msg, requests, journal };
}

test('Poshmark lists in one step with every field, no draft save, no tab', async () => {
  const { c, msg, requests, journal } = setup();
  const result = JSON.parse(JSON.stringify(await c.runPoshmarkApi(msg)));
  assert.deepEqual(result, { ok: true, state: 'published', listing_url: `https://poshmark.com/listing/${POST}` });
  assert.deepEqual(requests.map(r => `${r.method} ${r.path}`), ['GET /vm-rest/meta/catalog_v2', 'GET /create-listing', `POST /vm-rest/users/${USER}/posts`,
    `POST /api/posts/${POST}/media/scratch`, `POST /api/posts/${POST}/media/scratch`, `POST /vm-rest/posts/${POST}`,
    `PUT /vm-rest/posts/${POST}/status/published`, `GET /vm-rest/posts/${POST}`]);
  assert.ok(!requests.some(r => r.path.endsWith('/status/user_draft')));
  assert.ok(requests.filter(r => r.method !== 'GET').every(r => r.headers['X-XSRF-TOKEN'] === 'page-csrf'));
  assert.ok(requests.filter(r => r.method === 'GET').every(r => !('X-XSRF-TOKEN' in r.headers)));
  const post = requests[5].body.post;
  assert.deepEqual(post.catalog, { department: DEPT, category: SHIRTS, category_features: [TEES] });
  assert.equal(post.inventory.size_quantities[0].size_id, 'M');
  assert.deepEqual(post.inventory.size_quantities[0].size_set_tags, ['standard']);
  assert.equal(post.condition, 'ug');
  assert.deepEqual(post.colors, ['White', 'Gray']);
  assert.deepEqual(post.style_tags, ['Casual', 'Retro', 'Streetwear']);
  assert.deepEqual(post.price_amount, { val: '18', currency_code: 'USD', currency_symbol: '$' });
  assert.equal(post.original_price_amount.val, '0');
  assert.equal(post.offer_auto_actions_v2_enabled, true);
  assert.equal(post.cover_shot.id.length, 24);
  assert.equal(post.pictures.length, 1);
  assert.equal(journal['native_operation_op-1'].poshmark_post_id, POST);
});

test('an update writes the live Poshmark post and does not create or publish another', async () => {
  const { c, msg, requests } = setup();
  let saved = false;
  c.fetch = async (url, options = {}) => {
    const path = new URL(url).pathname;
    const method = options.method || 'GET';
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
    requests.push({ method, path, body });
    const reply = response;
    if (path === '/create-listing') return page();
    if (path === '/vm-rest/meta/catalog_v2') return reply(200, CATALOG);
    if (path.endsWith('/media/scratch')) return reply(200, { data: { id: 'c'.repeat(24) } });
    if (method === 'POST' && path === `/vm-rest/posts/${POST}`) { saved = true; return reply(200, { data: {} }); }
    if (method === 'GET' && path === `/vm-rest/posts/${POST}`) {
      return reply(200, { data: { id: POST, status: 'published', creator_id: USER, title: saved ? 'Vintage tee' : 'Old title',
        price_amount: { val: saved ? '18' : '10' }, inventory: { size_quantity_revision: 3, size_quantities: [{ quantity_sold: 0 }] } } });
    }
    return reply(500, {});
  };
  msg.payload.action = 'update';
  msg.payload.listing_id = POST;
  const result = JSON.parse(JSON.stringify(await c.runPoshmarkApi(msg)));
  assert.deepEqual(result, { ok: true, state: 'published', listing_url: `https://poshmark.com/listing/${POST}` });
  assert.deepEqual(requests.map(r => `${r.method} ${r.path}`), ['GET /vm-rest/posts/' + POST, 'GET /vm-rest/meta/catalog_v2', 'GET /create-listing',
    `POST /api/posts/${POST}/media/scratch`, `POST /api/posts/${POST}/media/scratch`, `POST /vm-rest/posts/${POST}`, `GET /vm-rest/posts/${POST}`]);
  const post = requests.find(r => r.method === 'POST' && r.path === `/vm-rest/posts/${POST}`).body.post;
  assert.equal(post.inventory.size_quantity_revision, 3);
  assert.equal(post.title, 'Vintage tee');
  assert.equal(post.original_price_amount.val, '0');
  assert.ok(!requests.some(r => r.path.includes('/users/') || r.path.endsWith('/status/published') || r.method === 'DELETE'));
});

test('big sizes are found in the Big & Tall set', () => {
  const { c } = setup();
  const category = CATALOG.data.catalog.departments[0].categories[0];
  const found = c.poshmarkSize(category, { size: 'XXL' });
  assert.deepEqual([found.size.id, found.set.tags[0]], ['XXL', 'big_&_tall']);
  assert.equal(c.poshmarkSize(category, { size: 'XL (18/20)' }).size.id, 'XL');
  assert.equal(c.poshmarkSize(category, { size: '40R' }), null);
});

test('missing category, size, price or confirmation stops before creating anything', async () => {
  for (const change of [{ marketplace_category_objects: {} }, { size: '40R' }, { price: 2 }, { description: '' }]) {
    const { c, msg, requests } = setup();
    msg.payload.listing = { ...LISTING, ...change };
    const result = await c.runPoshmarkApi(msg);
    assert.equal(result.safe_to_retry, true);
    assert.ok(!requests.some(r => r.method !== 'GET'));
  }
  const { c, msg, requests } = setup();
  msg.payload.confirm = false;
  assert.equal((await c.runPoshmarkApi(msg)).ok, false);
  assert.equal(requests.length, 0);
});

test('a refused publish removes the unlisted post and can be retried', async () => {
  const { c, msg, requests } = setup({ publishAnswer: [200, { error: { errorType: 'ProfilePhoneRequiredError', userMessage: 'Add a phone number' } }] });
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.error_code, 'rejected');
  assert.equal(result.marketplace_message, 'Add a phone number');
  assert.equal(requests.at(-1).method, 'DELETE');
});

test('a lost publish reply is never retried and recovery only reads', async () => {
  const { c, msg, requests, journal } = setup({ publishAnswer: [502, {}] });
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.safe_to_retry, false);
  assert.equal(result.error_code, 'uncertain');
  assert.match(result.error, new RegExp(POST));
  assert.equal(requests.filter(r => r.method === 'PUT').length, 1);
  assert.ok(!requests.some(r => r.method === 'DELETE'));
  requests.length = 0;
  assert.equal(journal['native_operation_op-1'].poshmark_post_id, POST);
  const recovered = await c.runPoshmarkApi(msg, true);
  assert.equal(recovered.state, 'published');
  assert.deepEqual(requests.map(r => r.method), ['GET']);
});

test('an expired Poshmark sign-in stops before any request', async () => {
  const { c, msg, requests } = setup();
  const claims = { user_id: USER, exp: Math.floor(Date.now() / 1000) - 60 };
  c.chrome.cookies = { get: async () => ({ value: `x.${Buffer.from(JSON.stringify(claims)).toString('base64')}.y` }) };
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.error_code, 'signed_out');
  assert.equal(result.safe_to_retry, true);
  assert.ok(!requests.some(r => r.method !== 'GET'));
});

test('a cancel after the post exists still removes it, with its own short limit', async () => {
  const { c, msg, requests } = setup();
  const original = c.fetch;
  c.fetch = async (url, options = {}) => {
    const answer = await original(url, options);
    if (new URL(url).pathname.endsWith('/media/scratch')) vm.runInContext('activeNativeOperation.cancelled = true', c);
    return answer;
  };
  vm.runInContext("activeNativeOperation = { id: 'op-1', cancelled: false, controller: new AbortController() }", c);
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, true);
  assert.equal(requests.at(-1).method, 'DELETE');
  assert.ok(!requests.some(r => r.method === 'PUT'));
});

test('a cleanup that fails leaves the result uncertain', async () => {
  const { c, msg } = setup({ publishAnswer: [200, { error: { errorType: 'ProfilePhoneRequiredError', userMessage: 'Add a phone number' } }] });
  const original = c.fetch;
  c.fetch = async (url, options = {}) => (options.method === 'DELETE' ? response(500, 'down') : original(url, options));
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.safe_to_retry, false);
  assert.equal(result.error_code, 'uncertain');
  assert.equal(result.marketplace_message, 'Add a phone number');
  assert.match(result.error, new RegExp(POST));
});

test('a photo upload refused with HTTP 400 reads the request token again and retries once', async () => {
  const { c, msg, requests } = setup();
  const original = c.fetch;
  let refused = 0;
  c.fetch = async (url, options = {}) => {
    if (new URL(url).pathname.endsWith('/media/scratch') && refused++ === 0) {
      requests.push({ method: 'POST', path: 'refused' });
      return response(400, { error: 'stale token' });
    }
    return original(url, options);
  };
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.ok, true);
  assert.equal(requests.filter(r => r.path === '/create-listing').length, 2);
});

test('a Poshmark sign-in page instead of the listing page means signed out', async () => {
  const { c, msg, requests } = setup();
  const original = c.fetch;
  c.fetch = async (url, options = {}) => (new URL(url).pathname === '/create-listing'
    ? response(200, '<html><script>window.__INITIAL_STATE__={"userCookies":{}};</script><form><input type="password"></form></html>', { 'content-type': 'text/html' })
    : original(url, options));
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.error_code, 'signed_out');
  assert.equal(result.safe_to_retry, true);
  assert.ok(!requests.some(r => r.method === 'POST'));
});

test('Poshmark requests from this extension carry its own Origin and Referer', async () => {
  const c = loadWorker();
  const updates = [];
  c.chrome.declarativeNetRequest.updateSessionRules = async value => { updates.push(value); };
  await c.ensureMarketplaceRequestContext();
  const rule = updates[0].addRules.find(value => value.id === 17005);
  assert.deepEqual([...rule.condition.initiatorDomains], ['test-extension-id']);
  const target = new RegExp(rule.condition.regexFilter);
  for (const url of ['https://poshmark.com/vm-rest/posts/x', 'https://poshmark.com/api/posts/x/media/scratch', 'https://poshmark.com/create-listing']) assert.ok(target.test(url), url);
  for (const url of ['https://poshmark.com.evil/vm-rest/', 'https://poshmark.com/feed']) assert.equal(target.test(url), false, url);
  assert.deepEqual(JSON.parse(JSON.stringify(rule.action.requestHeaders.map(header => [header.header, header.value]))),
    [['Origin', 'https://poshmark.com'], ['Referer', 'https://poshmark.com/create-listing']]);
});

test('a Poshmark validation refusal names what is missing and removes the unlisted post', async () => {
  const { c, msg, requests } = setup();
  const original = c.fetch;
  c.fetch = async (url, options = {}) => (options.method === 'POST' && new URL(url).pathname === `/vm-rest/posts/${POST}`
    ? response(200, { error: { errorType: 'ValidationError', userMessage: 'Please select a brand.' } }) : original(url, options));
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.error_code, 'missing_fields');
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.marketplace_message, 'Please select a brand.');
  assert.deepEqual(JSON.parse(JSON.stringify(result.fields)), [{ field: 'ValidationError', message: 'Please select a brand.' }]);
  assert.equal(requests.at(-1).method, 'DELETE');
});
