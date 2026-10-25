const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

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
  poshmark_specifics: { originalPrice: 30, styleTags: ['Casual', 'Retro', 'Streetwear', 'Extra'], smartSellMinimumPrice: 12 } };

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
    requests.push({ method, path, body: typeof options.body === 'string' ? JSON.parse(options.body) : options.body });
    const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
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
  assert.deepEqual(requests.map(r => `${r.method} ${r.path}`), ['GET /vm-rest/meta/catalog_v2', `POST /vm-rest/users/${USER}/posts`,
    `POST /api/posts/${POST}/media/scratch`, `POST /api/posts/${POST}/media/scratch`, `POST /vm-rest/posts/${POST}`,
    `PUT /vm-rest/posts/${POST}/status/published`, `GET /vm-rest/posts/${POST}`]);
  assert.ok(!requests.some(r => r.path.endsWith('/status/user_draft')));
  const post = requests[4].body.post;
  assert.deepEqual(post.catalog, { department: DEPT, category: SHIRTS, category_features: [TEES] });
  assert.equal(post.inventory.size_quantities[0].size_id, 'M');
  assert.deepEqual(post.inventory.size_quantities[0].size_set_tags, ['standard']);
  assert.equal(post.condition, 'ug');
  assert.deepEqual(post.colors, ['White', 'Gray']);
  assert.deepEqual(post.style_tags, ['Casual', 'Retro', 'Streetwear']);
  assert.deepEqual(post.price_amount, { val: '18', currency_code: 'USD', currency_symbol: '$' });
  assert.equal(post.original_price_amount.val, '30');
  assert.equal(post.offer_auto_actions_v2_enabled, true);
  assert.equal(post.cover_shot.id.length, 24);
  assert.equal(post.pictures.length, 1);
  assert.equal(journal['native_operation_op-1'].poshmark_post_id, POST);
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
  assert.match(result.error, /phone number/);
  assert.equal(requests.at(-1).method, 'DELETE');
});

test('a lost publish reply is never retried and recovery only reads', async () => {
  const { c, msg, requests, journal } = setup({ publishAnswer: [502, {}] });
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.safe_to_retry, false);
  assert.match(result.error, new RegExp(POST));
  assert.ok(!requests.some(r => r.method === 'DELETE'));
  requests.length = 0;
  assert.equal(journal['native_operation_op-1'].poshmark_post_id, POST);
  const recovered = await c.runPoshmarkApi(msg, true);
  assert.equal(recovered.state, 'published');
  assert.deepEqual(requests.map(r => r.method), ['GET']);
});
