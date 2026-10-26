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

const BRANDS = { data: [{ type: 'brand', brand_id: 'd'.repeat(24), brand_name: 'Hanes' }, { type: 'brand', brand_id: 'e'.repeat(24), brand_name: 'Hanes Her Way' }] };
const STYLE_TAGS = { data: ['Casual', 'Retro', 'Streetwear', 'Vintage'] };
const CATALOG_READS = ['GET /vm-rest/meta/catalog_v2', 'GET /vm-rest/searches/brands/suggested', 'GET /vm-rest/meta/style_tags/top'];
const savedPost = requests => requests.find(r => r.method === 'POST' && r.path === `/vm-rest/posts/${POST}`)?.body.post;
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
    if (path === '/vm-rest/searches/brands/suggested') return reply(200, BRANDS);
    if (path === '/vm-rest/meta/style_tags/top') return reply(200, STYLE_TAGS);
    if (method === 'POST' && path === `/vm-rest/users/${USER}/posts`) return reply(200, { data: { id: POST, status: 'draft' } });
    if (path.endsWith('/media/scratch')) return reply(200, { data: { id: `${'c'.repeat(23)}${photo++}` } });
    if (method === 'PUT' && path.endsWith('/status/published')) return reply(...publishAnswer);
    if (method === 'GET' && path === `/vm-rest/posts/${POST}`) {
      const saved = savedPost(requests);
      const readStatus = saved && !requests.some(r => r.path.includes('/status/')) ? 'draft' : status;
      return reply(200, { data: { ...saved, id: POST, creator_id: USER, status: readStatus } });
    }
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
  assert.deepEqual(requests.map(r => `${r.method} ${r.path}`), [...CATALOG_READS, 'GET /create-listing', `POST /vm-rest/users/${USER}/posts`,
    `POST /api/posts/${POST}/media/scratch`, `POST /api/posts/${POST}/media/scratch`, `POST /vm-rest/posts/${POST}`,
    `GET /vm-rest/posts/${POST}`, `PUT /vm-rest/posts/${POST}/status/published`, `GET /vm-rest/posts/${POST}`]);
  assert.ok(!requests.some(r => r.path.endsWith('/status/user_draft')));
  assert.ok(requests.filter(r => r.method !== 'GET').every(r => r.headers['X-XSRF-TOKEN'] === 'page-csrf'));
  assert.ok(requests.filter(r => r.method === 'GET').every(r => !('X-XSRF-TOKEN' in r.headers)));
  const post = savedPost(requests);
  assert.equal(post.brand, 'Hanes');
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

test('Save Draft uses the native user_draft transition and never publishes, including recovery', async () => {
  const { c, msg, requests } = setup({ status: 'user_draft' });
  msg.payload.action = 'save_draft';
  msg.payload.confirm = false;
  const result = JSON.parse(JSON.stringify(await c.runNativeMarketplace(msg)));
  assert.deepEqual(result, { ok: true, state: 'draft', draft_url: `https://poshmark.com/edit-listing/${POST}` });
  const transition = requests.find(r => r.path.endsWith('/status/user_draft'));
  assert.equal(transition.method, 'PUT');
  assert.equal(transition.body.post.title, LISTING.title);
  assert.ok(!requests.some(r => r.path.endsWith('/status/published') || r.method === 'DELETE'));
  requests.length = 0;
  // Preserve the saved post while observing that recovery performs GETs only.
  c.fetch = async (url, options = {}) => {
    assert.equal(options.method || 'GET', 'GET');
    if (new URL(url).pathname === `/vm-rest/posts/${POST}`) return response(200, { data: {
      ...transition.body.post, id: POST, creator_id: USER, status: 'user_draft',
    } });
    throw new Error('Unexpected recovery request');
  };
  assert.deepEqual(JSON.parse(JSON.stringify(await c.runPoshmarkApi(msg, true))), result);
});

test('Save Draft refuses a read-back mismatch and never falls through to publish', async () => {
  const { c, msg, requests } = setup({ status: 'published' });
  msg.payload.action = 'save_draft';
  msg.payload.confirm = false;
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, false);
  assert.match(result.error, /did not keep this draft revision/);
  assert.ok(!requests.some(r => r.path.endsWith('/status/published') || r.method === 'DELETE'));
});

test('an existing Poshmark draft is reused but a live or foreign post is never changed', async () => {
  for (const [status, owner, allowed] of [['user_draft', USER, true], ['published', USER, false], ['user_draft', 'f'.repeat(24), false]]) {
    const { c, msg, requests } = setup({ status: 'user_draft' });
    msg.payload.action = 'save_draft';
    msg.payload.confirm = false;
    msg.payload.draft_url = `https://poshmark.com/edit-listing/${POST}`;
    const fetch = c.fetch;
    let read = false;
    c.fetch = async (url, options = {}) => {
      if (!read && new URL(url).pathname === `/vm-rest/posts/${POST}` && (options.method || 'GET') === 'GET') {
        read = true;
        requests.push({ method: 'GET', path: new URL(url).pathname });
        return response(200, { data: { status, creator_id: owner, inventory: { size_quantity_revision: 4 } } });
      }
      return fetch(url, options);
    };
    const result = await c.runNativeMarketplace(msg);
    assert.equal(result.ok, allowed);
    assert.ok(!requests.some(r => r.path.includes('/users/') || r.path.endsWith('/status/published') || r.method === 'DELETE'));
    if (!allowed) assert.ok(requests.every(r => r.method === 'GET'));
    else assert.equal(savedPost(requests).inventory.size_quantity_revision, 4);
  }
});

test('Poshmark draft verification catches changed size, quantity, colors, price and photo count', () => {
  const { c } = setup();
  const expected = c.poshmarkPostFields(CATALOG.data, LISTING, { brand: 'Hanes', styleTags: [] });
  const actual = JSON.parse(JSON.stringify(expected));
  actual.cover_shot = { id: 'photo' };
  actual.pictures = [];
  assert.equal(c.poshmarkPostDifferences(actual, expected, 1).length, 0);
  actual.inventory.size_quantities[0].size_id = 'XL';
  actual.inventory.size_quantities[0].quantity_available = 2;
  actual.colors = ['Blue'];
  actual.price_amount.val = '99';
  assert.deepEqual(Array.from(c.poshmarkPostDifferences(actual, expected, 2)), ['sizes', 'colors', 'price', 'photos']);
});

test('clearing native Poshmark values does not restore general or old form defaults', async () => {
  const { c } = setup();
  const listing = { ...LISTING, brand: '', form: { overrides: { brand: 'Old brand', condition: 'ug' } },
    poshmark_specifics: { ...LISTING.poshmark_specifics, poshmarkColor: '', poshmarkSecondaryColor: '', poshmarkPrice: 0, condition: '' } };
  assert.equal(await c.poshmarkBrand(listing, Date.now() + 30000), '');
  assert.equal(c.poshmarkPrice(listing), 0);
  assert.equal(c.poshmarkCondition(listing), null);
  listing.poshmark_specifics.poshmarkPrice = 18;
  listing.poshmark_specifics.condition = 'ug';
  const post = c.poshmarkPostFields(CATALOG.data, listing, { brand: '' });
  assert.equal(post.brand, '');
  assert.deepEqual(Array.from(post.colors), []);
});

test('a changed draft field prevents the confirmed publication request', async () => {
  const { c, msg, requests } = setup();
  const fetch = c.fetch;
  c.fetch = async (url, options = {}) => {
    if (new URL(url).pathname === `/vm-rest/posts/${POST}` && (options.method || 'GET') === 'GET') {
      return response(200, { data: { ...savedPost(requests), id: POST, creator_id: USER, status: 'draft', brand: 'Different brand' } });
    }
    return fetch(url, options);
  };
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.ok, false);
  assert.match(result.error, /brand.*not published/);
  assert.ok(!requests.some(r => r.path.endsWith('/status/published')));
});

test('Poshmark sends the selected quantity, original price and explicit SKU instead of generated defaults', () => {
  const c = loadWorker();
  const listing = { ...LISTING, quantity: 3, sku: 'item-details-sku', seller_fields: ['poshmark_specifics.originalPrice'], poshmark_specifics: {
    ...LISTING.poshmark_specifics, originalPrice: 55, poshmarkSku: '',
  } };
  const post = c.poshmarkPostFields(CATALOG.data, listing, { brand: 'Hanes', styleTags: [] });
  assert.equal(post.inventory.multi_item, true);
  assert.equal(post.inventory.size_quantities[0].quantity_available, 3);
  assert.equal(post.original_price_amount.val, '55');
  assert.deepEqual(JSON.parse(JSON.stringify(post.seller_private_info)), { sku: '' });
  assert.throws(() => c.poshmarkPostFields(CATALOG.data, { ...listing, quantity: 1.5 }, {}), /whole-number quantity/);
});

test('Poshmark private notes and cost support clearing, and shipping discounts require a live option', async () => {
  const { c } = setup();
  const listing = { ...LISTING, cost: 0, poshmark_specifics: { ...LISTING.poshmark_specifics,
    poshmarkSku: '', sellerNotes: '', availability: 'not_for_sale', shippingDiscountId: 'd'.repeat(24),
  } };
  c.poshmarkShippingDiscounts = async () => [{ id: 'd'.repeat(24), label: 'Buyer Shipping: Free' }];
  const post = await c.poshmarkPreparedPost(listing, Date.now() + 30000);
  assert.equal(post.inventory.status, 'not_for_sale');
  assert.equal(post.offer_auto_actions_v2_enabled, false);
  assert.deepEqual(JSON.parse(JSON.stringify(post.seller_private_info)), {
    sku: '', other_info: '', cost_price_amount: { val: '0.00', currency_code: 'USD', currency_symbol: '$' },
  });
  assert.equal(post.seller_shipping_discount.id, 'd'.repeat(24));
  listing.cost = '';
  listing.poshmark_specifics.shippingDiscountId = '';
  c.poshmarkShippingDiscounts = async () => [{ id: 'e'.repeat(24), discount_amount: { val: '0.00' } }];
  const cleared = await c.poshmarkPreparedPost(listing, Date.now() + 30000);
  assert.equal(cleared.seller_private_info.cost_price_amount, null);
  assert.equal(cleared.seller_shipping_discount.id, 'e'.repeat(24));
  assert.deepEqual(Array.from(c.poshmarkPostDifferences(cleared, cleared, 0)), []);
  assert.throws(() => c.poshmarkPostFields(CATALOG.data, { ...listing, cost: -1 }, {}), /cost of \$0/);
  c.poshmarkShippingDiscounts = async () => [];
  await assert.rejects(c.poshmarkPreparedPost(listing, Date.now() + 30000), /current shipping discount/);
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
    if (path === '/vm-rest/searches/brands/suggested') return reply(200, BRANDS);
    if (path === '/vm-rest/meta/style_tags/top') return reply(200, STYLE_TAGS);
    if (path.endsWith('/media/scratch')) return reply(200, { data: { id: 'c'.repeat(24) } });
    if (method === 'POST' && path === `/vm-rest/posts/${POST}`) { saved = true; return reply(200, { data: {} }); }
    if (method === 'GET' && path === `/vm-rest/posts/${POST}`) {
      return reply(200, { data: { id: POST, status: 'published', creator_id: USER, title: saved ? 'Vintage tee' : 'Old title',
        price_amount: { val: saved ? '18.0' : '10' }, inventory: { size_quantity_revision: 3, size_quantities: [{ quantity_sold: 0 }] } } });
    }
    return reply(500, {});
  };
  msg.payload.action = 'update';
  msg.payload.listing_id = POST;
  msg.payload.listing = { ...LISTING, price: 17.6 };
  const result = JSON.parse(JSON.stringify(await c.runPoshmarkApi(msg)));
  assert.deepEqual(result, { ok: true, state: 'published', listing_url: `https://poshmark.com/listing/${POST}` });
  assert.deepEqual(requests.map(r => `${r.method} ${r.path}`), ['GET /vm-rest/posts/' + POST, ...CATALOG_READS, 'GET /create-listing',
    `POST /api/posts/${POST}/media/scratch`, `POST /api/posts/${POST}/media/scratch`, `POST /vm-rest/posts/${POST}`, `GET /vm-rest/posts/${POST}`]);
  const post = requests.find(r => r.method === 'POST' && r.path === `/vm-rest/posts/${POST}`).body.post;
  assert.equal(post.inventory.size_quantity_revision, 3);
  assert.equal(post.title, 'Vintage tee');
  assert.equal(post.original_price_amount.val, '0');
  assert.ok(!requests.some(r => r.path.includes('/users/') || r.path.endsWith('/status/published') || r.method === 'DELETE'));
});

test('Poshmark gets whole dollars, rounded the way Vendoo rounds', async () => {
  const { c, msg, requests } = setup();
  msg.payload.listing = { ...LISTING, price: 17.5 };
  assert.equal((await c.runPoshmarkApi(msg)).ok, true);
  assert.equal(savedPost(requests).price_amount.val, '18');
  assert.equal(c.poshmarkPrice({ price: 19.49 }), 19);
  assert.equal(c.poshmarkPrice({ price: 19.99, poshmark_specifics: { poshmarkPrice: '24.5' } }), 25);
});

test('Poshmark conditions use its own codes and never drop a plain New', () => {
  const { c } = setup();
  const labels = { 'New With Tags/Box': 'nwt', 'New Without Tags/Box': 'uln', 'New With Imperfections': 'ug',
    'Pre-Owned - Excellent': 'uln', 'Pre-Owned - Good': 'ug', 'Pre-Owned - Fair': 'uf', 'Poor (Major flaws)': 'uf',
    New: 'nwt', 'Brand new': 'nwt', 'Pre-owned': 'ug', Mint: null };
  for (const [label, code] of Object.entries(labels)) assert.equal(c.poshmarkCondition({ condition: label }), code, label);
  assert.equal(c.poshmarkCondition({ condition: 'Pre-Owned - Good', form: { overrides: { condition: 'uf' } } }), 'uf');
});

test('big sizes are found in the Big & Tall set', () => {
  const { c } = setup();
  const category = CATALOG.data.catalog.departments[0].categories[0];
  const found = c.poshmarkSize(category, { size: 'XXL' });
  assert.deepEqual([found.size.id, found.set.tags[0]], ['XXL', 'big_&_tall']);
  assert.equal(c.poshmarkSize(category, { size: 'XL (18/20)' }).size.id, 'XL');
  assert.equal(c.poshmarkSize(category, { size: '40R' }), null);
});

test('missing category, size, price, condition or confirmation stops before creating anything', async () => {
  for (const change of [{ marketplace_category_objects: {} }, { size: '40R' }, { price: 2 }, { description: '' }, { condition: '' }]) {
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

// A slice of Poshmark's live catalog_v2 (read 2026-10-25).
const LIVE = require('./fixtures/poshmark-catalog.json');
const liveCategory = (department, name) => {
  const dept = LIVE.catalog.departments.find(entry => entry.display === department);
  return { dept, category: dept.categories.find(entry => entry.display === name) };
};

test('Poshmark sizes stay inside the department, gender and fit the listing names', () => {
  const { c } = setup();
  const kids = liveCategory('Kids', 'Shirts & Tops').category;
  const size = (category, listing, kidsDept = false) => {
    const found = c.poshmarkSize(category, listing, { kids: kidsDept });
    return found?.size ? `${found.size.id}:${found.set.tags[0]}` : found?.problem || null;
  };
  // The seller's boys' XL (18/20) once landed in a girls' size.
  assert.equal(size(kids, { size: 'XL (18/20)', department: 'Boys' }, true), 'XLB:boys');
  assert.equal(size(kids, { size: 'XL', category_path: 'Clothing > Kids > Girls > Tops' }, true), 'XLG:girls');
  assert.match(size(kids, { size: 'XL (18/20)' }, true), /boys' or girls'/);
  assert.equal(size(kids, { size: '6-9 Months', department: 'Girls' }, true), '6-9MB:baby');
  const tops = liveCategory('Women', 'Tops').category;
  assert.equal(size(tops, { size: 'M', sizeType: 'Petite' }), 'MP:petite');
  assert.equal(size(tops, { size: 'Medium' }), 'M:standard');
  assert.equal(size(tops, { size: '2XL' }), 'XXL:plus');
  assert.equal(size(tops, { size: '2X', sizeType: 'Plus' }), '2X:plus');
  assert.match(size(tops, { size: 'L', sizeType: 'Big & Tall' }), /no Big & Tall sizes/);
  assert.equal(size(tops, { size: 'L/XL' }), null);
  const shirts = liveCategory('Men', 'Shirts').category;
  assert.equal(size(shirts, { size: '2XL' }), 'XXL:big_&_tall');
  assert.equal(size(shirts, { size: 'Large' }), 'L:standard');
  const jeans = liveCategory('Men', 'Jeans').category;
  assert.equal(size(jeans, { size: '32x30' }), '32:standard');
  assert.equal(size(jeans, { size: 'Waist 32', poshmark_specifics: { sizeType: 'Regular' } }), '32:standard');
  assert.equal(size(tops, { size: 'M', poshmark_specifics: { sizeType: 'Petite' } }), 'MP:petite');
  assert.equal(size(jeans, { size: 'W38 L32', sizeType: 'Big & Tall' }), '38BT:big_&_tall');
  assert.equal(size(liveCategory('Women', 'Jeans').category, { size: 'US 10' }), '10:standard');
});

test('Vendoo\'s coded Poshmark size wins over the size text', () => {
  const { c } = setup();
  const kids = liveCategory('Kids', 'Shirts & Tops').category;
  const listing = { size: 'XL', form: { aspects: { [`${kids.id}_size`]: 'XLB', [`${kids.id}_size_scale`]: 'boys' } } };
  assert.equal(c.poshmarkSize(kids, listing, { kids: true }).size.id, 'XLB');
  const custom = { form: { aspects: { [`${kids.id}_size`]: 'Fits 10-12', [`${kids.id}_size_scale`]: 'customSize' } } };
  assert.deepEqual(JSON.parse(JSON.stringify(c.poshmarkSize(kids, custom, { kids: true }).size)), { id: 'Fits 10-12', display: 'Fits 10-12' });
});

test('a direct Poshmark category is used, and a stale id falls back to the live names', () => {
  const { c } = setup();
  const liveId = 'a'.repeat(24);
  const stale = 'b'.repeat(24);
  const catalog = { catalog: { departments: [{ id: DEPT, display: 'Men', categories: [{ id: SHIRTS, display: 'Shirts',
    category_features: [{ id: liveId, display: 'Tees - Short Sleeve' }] }] }] } };
  const stored = { direct_category_ids: { poshmark: `${DEPT}/${SHIRTS}/${liveId}` },
    marketplace_category_objects: { poshmark: { path: [DEPT, SHIRTS, stale] } } };
  assert.equal(c.poshmarkCategory(catalog, stored).leaf, liveId);
  assert.deepEqual(stored.marketplace_category_objects.poshmark.path, [DEPT, SHIRTS, stale]);
  const named = { marketplace_category_objects: { poshmark: { path: [stale, stale, stale],
    displayPath: ['Men', 'Shirts', 'Tees - Short Sleeve'] } } };
  assert.equal(c.poshmarkCategory(catalog, named).leaf, liveId);
});

test('a Global & Traditional Wear leaf skips the grouping id', () => {
  const { c } = setup();
  const catalog = { catalog: { departments: [{ id: DEPT, display: 'Men', categories: [{ id: 'k'.repeat(24), display: 'Kurtas', size_sets: [] }] }] } };
  const listing = { form: { overrides: { categoryV2: { path: [DEPT, `group:${DEPT}:global_&_traditional_wear`, 'k'.repeat(24)] } } } };
  assert.deepEqual(JSON.parse(JSON.stringify(c.poshmarkCategory(catalog, listing).catalog)),
    { department: DEPT, category: 'k'.repeat(24), category_features: [] });
});

test('a kids listing with no gender stops before anything is written and names the size', async () => {
  const kids = liveCategory('Kids', 'Shirts & Tops');
  const { c, msg, requests } = setup();
  const original = c.fetch;
  c.fetch = async (url, options = {}) => (new URL(url).pathname === '/vm-rest/meta/catalog_v2' ? response(200, LIVE) : original(url, options));
  msg.payload.listing = { ...LISTING, size: 'XL (18/20)', marketplace_category_objects: { poshmark: { path: [kids.dept.id, kids.category.id] } } };
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.error_code, 'missing_fields');
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.fields[0].field, 'size');
  assert.match(result.fields[0].message, /boys' or girls'/);
  assert.ok(!requests.some(r => r.method !== 'GET'));
});

test('Poshmark brand, style tags, certification and limits follow Poshmark\'s own catalog', async () => {
  const { c, msg, requests } = setup();
  msg.payload.listing = { ...LISTING, brand: 'hanes', tags: ['vintage', 'casual', 'not#ok', 'graphic'],
    poshmark_specifics: { ...LISTING.poshmark_specifics, styleTags: undefined } };
  assert.equal((await c.runPoshmarkApi(msg)).ok, true);
  const post = savedPost(requests);
  assert.equal(post.brand, 'Hanes');
  assert.deepEqual(post.style_tags, ['Vintage', 'Casual']);
  assert.ok(requests.some(r => r.method === 'PUT' && r.path.endsWith('/status/published')));
  const { c: other, msg: second, requests: none } = setup();
  second.payload.listing = { ...LISTING, description: 'x'.repeat(1501) };
  const refused = await other.runPoshmarkApi(second);
  assert.equal(refused.error_code, 'missing_fields');
  assert.ok(!none.some(r => r.method !== 'GET'));
});

test('a Poshmark catalog that cannot be read stops safely and says why', async () => {
  for (const [answer, code] of [[[403, '<html>Forbidden</html>', { 'content-type': 'text/html' }], 'challenge'],
    [[503, {}], 'network'], [[429, {}, { 'retry-after': '30' }], 'rate_limited'], [[200, { data: { nope: true } }], 'schema_drift']]) {
    const { c, msg, requests } = setup();
    const original = c.fetch;
    c.fetch = async (url, options = {}) => (new URL(url).pathname === '/vm-rest/meta/catalog_v2' ? response(...answer) : original(url, options));
    const result = await c.runPoshmarkApi(msg);
    assert.equal(result.error_code, code, code);
    assert.equal(result.safe_to_retry, true);
    assert.ok(!requests.some(r => r.method !== 'GET'));
  }
});

test('Smart Pricing follows the listing form, off included, and cost keeps its cents', () => {
  const { c } = setup();
  const fields = listing => c.poshmarkPostFields(CATALOG.data, listing);
  const off = fields({ ...LISTING, cost: 3.65, form: { specifics: { smartSell: { enabled: false, minPrice: '' } } } });
  assert.equal(off.offer_auto_actions_v2_enabled, false);
  assert.equal(off.offer_auto_actions_min_price_amount, null);
  assert.deepEqual(JSON.parse(JSON.stringify(off.seller_private_info)),
    { cost_price_amount: { val: '3.65', currency_code: 'USD', currency_symbol: '$' } });
  const on = fields({ ...LISTING, form: { specifics: { smartSell: { enabled: true, minPrice: '14' } } } });
  assert.equal(on.offer_auto_actions_v2_enabled, true);
  assert.equal(on.offer_auto_actions_min_price_amount.val, '14');
  assert.equal(on.seller_private_info, undefined);
  // No form setting: the Poshmark specifics' minimum (12 of 18).
  assert.equal(fields({ ...LISTING, form: {} }).offer_auto_actions_min_price_amount.val, '12');
  // A minimum at or above the price is off.
  assert.equal(fields({ ...LISTING, form: { specifics: { smartSell: { enabled: true, minPrice: 18 } } } }).offer_auto_actions_v2_enabled, false);
});

test('a published post that did not keep Smart Pricing is listed with a warning', async () => {
  const { c, msg, requests } = setup();
  const fetch = c.fetch;
  c.fetch = async (url, options = {}) => {
    const path = new URL(url).pathname;
    if ((options.method || 'GET') === 'GET' && path === `/vm-rest/posts/${POST}`) {
      return response(200, { data: { ...savedPost(requests), id: POST, creator_id: USER,
        status: requests.some(r => r.path.endsWith('/status/published')) ? 'published' : 'draft',
        offer_auto_actions_v2_enabled: true, offer_auto_actions_min_price_amount: { val: '9' } } });
    }
    return fetch(url, options);
  };
  msg.payload.listing = { ...LISTING, form: { specifics: { smartSell: { enabled: false } } } };
  const result = await c.runPoshmarkApi(msg);
  assert.equal(result.ok, true);
  assert.match(result.warning, /left Smart Pricing on/);
  requests.length = 0;
  msg.payload.listing = LISTING;
  msg.payload.operation_id = 'op-2';
  const kept = await c.runPoshmarkApi(msg);
  assert.match(kept.warning, /\$12 minimum/);
});

test('the Poshmark tab\'s condition, colors and sub-category win over the shared values', () => {
  const { c } = setup();
  const LONG = '44009813d97b4e3995005764';
  const catalog = JSON.parse(JSON.stringify(CATALOG.data));
  catalog.catalog.departments[0].categories[0].category_features.push({ id: LONG, display: 'Tees - Long Sleeve' });
  const listing = { ...LISTING, form: { overrides: { condition: 'ug' } },
    poshmark_specifics: { poshmarkCondition: 'Like New', poshmarkColor: 'Blue', poshmarkSecondaryColor: 'Grey', subcategory: 'Tees - Long Sleeve' } };
  const post = c.poshmarkPostFields(catalog, listing);
  assert.equal(post.condition, 'uln');
  assert.deepEqual(JSON.parse(JSON.stringify(post.colors)), ['Blue', 'Gray']);
  assert.deepEqual(JSON.parse(JSON.stringify(post.catalog)), { department: DEPT, category: SHIRTS, category_features: [LONG] });
  const none = c.poshmarkCategory(catalog, { ...listing, poshmark_specifics: { subcategory: 'None' } });
  assert.deepEqual(JSON.parse(JSON.stringify(none.catalog.category_features)), []);
  const unknown = c.poshmarkCategory(catalog, { ...listing, poshmark_specifics: { subcategory: 'Polos' } });
  assert.equal(unknown.leaf, TEES);
});
