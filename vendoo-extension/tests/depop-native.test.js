const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call, reply } = require('./worker-context');
const DRAFT = 'https://www.depop.com/sellinghub/drafts/edit/123456/';
const listing = { title: 'Test', description: 'Private draft', price: 12 };
// Depop's taxonomy in the shape its taxonomy guide documents (api/v3/attributes).
const ATTRIBUTES = {
  department: [{ id: 'menswear' }, { id: 'womenswear' }, { id: 'kidswear' }, { id: 'everything-else' }],
  group: [{ id: 'tops', department: ['menswear', 'womenswear', 'kidswear'], product_types: [
    { id: 'tshirts', attribute_ids: ['material', 'occasion', 'body-fit'] }] },
  { id: 'bottoms', department: ['menswear', 'womenswear', 'kidswear'], product_types: [{ id: 'jeans', attribute_ids: ['bottom-fit', 'material'] }] },
  { id: 'dresses', department: ['womenswear'], product_types: [{ id: 'dresses', attribute_ids: [] }] }],
  generic_attributes: [
    { id: 'material', max_selection: 3, is_mandatory: false, attributes: [{ id: 'cotton', name_i18n: { 'en-US': 'Cotton' } }, { id: 'polyester', name_i18n: { 'en-US': 'Polyester' } }] },
    { id: 'occasion', max_selection: 1, is_mandatory: false, attributes: [{ id: 'casual', name_i18n: { 'en-US': 'Casual' } }, { id: 'party', name_i18n: { 'en-US': 'Party' } }] },
    { id: 'body-fit', max_selection: 1, is_mandatory: false, attributes: [{ id: 'petite', name_i18n: { 'en-US': 'Petite' } }, { id: 'plus-size', name_i18n: { 'en-US': 'Plus size' } }] },
    { id: 'bottom-fit', max_selection: 1, is_mandatory: true, attributes: [{ id: 'skinny', name_i18n: { 'en-US': 'Skinny' } }] }],
  category_size_mapping: [{ department: 'menswear', group: 'tops', product_type: 'tshirts', size_set_by_region: { US: 12, GB: 13 } },
    { department: 'womenswear', group: 'tops', product_type: 'tshirts', size_set_by_region: { US: 22 } },
    { department: 'kidswear', group: 'tops', product_type: 'tshirts', size_set_by_region: { US: 32 } },
    { department: 'womenswear', group: 'dresses', product_type: 'dresses' }],
  size_sets: [{ id: 12, sizes: [{ id: 4, name_i18n: { 'en-US': 'M' } }, { id: 6, name_i18n: { 'en-US': 'XL' } }, { id: 7, name_i18n: { 'en-US': 'XXL' } }] },
    { id: 22, sizes: [{ id: 3, name_i18n: { 'en-US': 'S' } }, { id: 4, name_i18n: { 'en-US': 'M' } }] },
    { id: 32, sizes: [{ id: 14, name_i18n: { 'en-US': '12 years' } }, { id: 15, name_i18n: { 'en-US': 'XL' } }] }],
  brand: [{ id: 'nike', name: 'Nike' }, { id: 'other', name: 'Other' }],
};
const FULL = { ...listing, brand: 'Nike', size: '2XL', condition: 'Pre-Owned - Good', primaryColor: 'Multicolor', tags: ['vintage tee', 'Y2K'],
  depop_specifics: { parcelSize: 'Medium', material: ['Cotton'], occasion: ['Casual', 'Party'] },
  form: { overrides: { condition: 'used_good', brand: 'Nike', categoryV2: { id: 'menswear_tshirts', path: ['menswear', 'menswear_tops', 'menswear_tshirts'] } },
    specifics: { style: ['casual'] }, aspects: {}, ships_from_zip: '70115' } };

// Answers Depop's catalog and the seller's shipping reads; other requests go on.
function withCatalog(c, { attributes = [200, ATTRIBUTES], addresses = [200, [{ id: 41, postal_code: '70115' }, { id: 42, postal_code: '10001' }]],
  providers = [200, [{ id: 'USPS', parcelSizes: [{ id: 'small', title: 'Extra small' }, { id: 'medium', title: 'Medium' }] }]] } = {}) {
  const original = c.fetch;
  const reads = [];
  c.fetch = async (url, options = {}) => {
    const { hostname, pathname } = new URL(url);
    if (hostname === 'api.depop.com' && pathname === '/api/v3/attributes/') { reads.push('attributes'); return reply(...attributes); }
    if (pathname === '/api/v1/addresses/') { reads.push('addresses'); return reply(...addresses); }
    if (pathname === '/api/v1/shipping-providers/') { reads.push('providers'); return reply(...providers); }
    return original(url, options);
  };
  return reads;
}

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
  const body = JSON.parse(requests.find(request => request.method === 'PUT').body);
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
  assert.equal(JSON.parse(requests.find(request => request.method === 'PUT').body).condition, 'used_like_new');
});

test('Depop draft updates preserve photos and never call the publish endpoint', async () => {
  const { c, requests, payload } = page();
  const result = await c.depopApiAction('save', payload);
  assert.equal(result.photo_count, 1);
  const methods = requests.map(r => r.method);
  assert.deepEqual(methods.filter(method => method === 'PUT'), ['PUT']);
  assert.equal(methods.at(-1), 'GET');
  assert.equal(JSON.parse(requests.find(request => request.method === 'PUT').body).pictures[0], 789);
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
  withCatalog(c);
  const msg = { job_id: 'job-1', payload: { action: 'save_draft', operation_id: 'abc123', listing: FULL,
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
  assert.equal(requests.filter(request => request.method === 'PUT').length, 1);
});

test('a deleted Depop draft is replaced once and the missing link is not kept', async () => {
  const gone = 'https://www.depop.com/sellinghub/drafts/edit/123456/';
  const created = 'https://www.depop.com/sellinghub/drafts/edit/newdraft1/';
  const { c, requests, msg } = newDraft();
  msg.payload.draft_url = gone;
  let kept = { id: 'newdraft1', description: 'Test\n\nPrivate draft', price_amount: '12', pictures: [{ id: 9 }] };
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    requests.push({ method: options.method, pathname });
    if (pathname === '/presentation/api/v1/drafts/123456/' || pathname === '/presentation/api/v1/drafts/123456') {
      return reply(404, { message: 'Could not find draft with id: 123456' });
    }
    if (options.method === 'POST' && pathname === '/presentation/api/v1/drafts/') return reply(201, { id: 'newdraft1' });
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      kept = { ...kept, ...body, pictures: (body.pictures || []).map(id => ({ id })) };
    }
    return reply(200, kept);
  };
  withCatalog(c);
  const result = await c.runDepopNative(msg);
  assert.equal(result.ok, true);
  assert.equal(result.draft_url, created);
  assert.equal(result.draft_replaced, true);
  assert.equal(requests.filter(request => request.method === 'POST' && request.pathname === '/presentation/api/v1/drafts/').length, 1);
  assert.ok(!JSON.stringify(result).includes('123456'));

  requests.length = 0;
  const published = await c.runDepopNative({ job_id: 'job-1', payload: { ...msg.payload, action: 'publish', confirm: true, draft_url: gone } });
  assert.equal(published.ok, false);
  assert.equal(published.draft_url, undefined);
  assert.match(published.marketplace_message, /could not find draft/i);
  assert.ok(!requests.some(request => request.pathname.includes('/drafts/products/')));
});

test('Depop updates and reads an existing private draft without any tab or page execution', async () => {
  const { c, payload } = page();
  c.chrome.tabs = new Proxy({}, { get: () => () => { assert.fail('No marketplace tabs'); } });
  c.chrome.scripting = { executeScript: () => { assert.fail('No page execution'); } };
  c.chrome.storage.local.get = async () => ({});
  withCatalog(c);
  const result = await c.runDepopNative({ payload: { ...payload, listing: FULL, operation_id: 'abc123' } });
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

test('a boys jacket draft sends Depop\'s category, brand, location, and parcel', async () => {
  const { c, requests, payload } = page();
  payload.listing = {
    ...listing, brand: 'Columbia', department: 'Boys', price: 12,
    depop_specifics: { categoryId: 'kidswear_jackets', parcelSize: 'Medium' },
    marketplace_category_ids: { depop: 'kidswear_jackets' },
  };
  const brands = [
    { id: 'columbia-sportswear', name: 'Columbia Sportswear', status: 'active' },
    { id: 'marshall-columbia', name: 'Marshall Columbia', status: 'active' },
  ];
  c.fetch = async (url, options = {}) => {
    requests.push({ url, ...options });
    const path = new URL(url).pathname;
    if (options.method === 'PUT') return reply(200, { id: 123456, description: 'Test\n\nPrivate draft', price_amount: '12', pictures: [{ id: 789 }], missing_fields: [] });
    if (path === '/presentation/api/v1/attributes/') return reply(200, { brand: brands });
    if (path === '/presentation/api/v1/users/me/') return reply(200, { country: 'US' });
    if (path.endsWith('/countries.json')) return reply(200, [{ country: 'United States', countryCode: 'US', address: 'United States', geoLat: 39.3, geoLng: -97.9 }]);
    if (path === '/presentation/api/v1/users/me/settings/shipping/') return reply(200, { shipping_providers: [{ id: 'USPS', active: true }, { id: 'MANUAL', active: false }] });
    if (path === '/presentation/api/v1/addresses/') {
      return reply(200, [
        { id: 1, last_modified: '2020-01-01T00:00:00Z' },
        { id: 2, last_modified: '2024-01-01T00:00:00Z' },
      ]);
    }
    if (path === '/presentation/api/v1/shipping/providers/depop/') {
      return reply(200, [{ id: 'USPS', parcel_sizes: [{ id: 'under_1lb', title: 'Medium', cost: { amount: '1.99' } }, { id: 'medium', title: 'Large', cost: { amount: '5.99' } }] }]);
    }
    return reply(200, { id: 123456, description: 'Test\n\nPrivate draft', price_amount: '12', pictures: [{ id: 789 }], missing_fields: [] });
  };
  await c.depopApiAction('save', payload);
  const body = JSON.parse(requests.find(request => request.method === 'PUT').body);
  assert.equal(body.product_type, 'jackets');
  assert.equal(body.is_kids, true);
  assert.equal(body.gender, 'male');
  assert.equal(body.brand, 'columbia-sportswear');
  assert.equal(body.address, 'United States');
  assert.equal(body.country_code, 'US');
  assert.equal(body.geo_lat, 39.3);
  assert.deepEqual(body.shipping_methods, [{
    payer: 'buyer', parcel_size: 'under_1lb', shipping_provider_id: 'USPS', ship_from_address_id: 2,
  }]);
  assert.equal(body.national_shipping_cost, '1.99');
  const quote = requests.find(request => request.url.includes('/shipping/providers/depop/'));
  assert.equal(new URL(quote.url).searchParams.get('item_cost'), '12.00');
  assert.equal(quote.headers['x-country-code'], 'US');
  assert.equal(call(c, 'depopBrandId', brands, 'Columbia'), 'columbia-sportswear');
  assert.equal(call(c, 'depopBrandId', brands, 'Columbia Sportswear'), 'columbia-sportswear');
  const split = [
    { id: 'a', name: 'Columbia One', status: 'active' },
    { id: 'b', name: 'Columbia Two', status: 'active' },
    { id: 'brand-other', name: 'Other', status: 'active' },
  ];
  assert.equal(call(c, 'depopBrandId', split, 'Columbia'), 'brand-other');
  assert.equal(call(c, 'depopBrandId', split, ''), 'brand-other');
  assert.equal(call(c, 'depopBrandId', split, 'Not A Real Brand'), 'brand-other');
  assert.equal(call(c, 'depopBrandId', [{ id: 'nike', name: 'Nike' }], 'Unbranded'), 'other');
  const translated = [
    { id: 'nike', name_i18n: { 'en-US': 'Nike' } },
    { id: 'other', name_i18n: { 'en-US': 'Other' } },
  ];
  assert.equal(call(c, 'depopBrandId', translated, 'Nike'), 'nike');
  assert.equal(call(c, 'depopBrandId', translated, 'GB Girls'), 'other');
  assert.equal(call(c, 'depopBrand', { brand: 'Not A Real Brand' }, { brand: split }), 'brand-other');
});

test('a direct Depop category wins, and a stale id falls back to the live product type name', () => {
  const c = loadWorker();
  const direct = call(c, 'depopCategory', {
    direct_category_ids: { depop: 'womenswear_dresses' },
    marketplace_category_ids: { depop: 'menswear_tshirts' },
    form: { overrides: { categoryV2: { id: 'menswear_tshirts', path: ['menswear', 'menswear_tops', 'menswear_tshirts'] } } },
  }, ATTRIBUTES);
  assert.equal(direct.productType, 'dresses');
  assert.equal(direct.department, 'womenswear');
  const named = call(c, 'depopCategory', {
    marketplace_categories: { depop: 'Men > Tops > T-Shirts' },
    marketplace_category_objects: { depop: { id: 'old_leaf', path: ['old'] } },
  }, ATTRIBUTES);
  assert.equal(named.productType, 'tshirts');
  assert.equal(named.department, 'menswear');
});
