const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call, reply } = require('./worker-context');
const DRAFT = 'https://www.depop.com/sellinghub/drafts/edit/123456/';
const listing = { title: 'Test', description: 'Private draft', price: 12 };
// Depop's taxonomy, as the listing site splits it: attributes, then size sets.
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

test('Depop sends and verifies exact SKU and quantity, including clearing an Item Details SKU', async () => {
  const { c } = newDraft();
  withCatalog(c);
  const item = { ...FULL, quantity: 3, sku: 'item-details', depop_specifics: { ...FULL.depop_specifics, sku: '' } };
  const fields = await c.depopResolvedFields({ listing: item, deadline: Date.now() + 30000 });
  assert.equal(fields.sku, '');
  assert.deepEqual(JSON.parse(JSON.stringify(Object.values(fields.variants))), [3]);
  const draft = { ...fields, description: c.depopDescription(item), price_amount: String(item.price), variant_set_id: fields.variant_set };
  assert.deepEqual(Array.from(c.depopDraftMismatch(draft, item, fields)), []);
  const size = Object.keys(fields.variants)[0];
  assert.ok(c.depopDraftMismatch({ ...draft, variants: { [size]: 1 } }, item, fields).includes('quantity'));
  assert.ok(c.depopDraftMismatch({ ...draft, sku: 'old-sku' }, item, fields).includes('SKU'));
  await assert.rejects(c.depopResolvedFields({ listing: { ...item, quantity: 0 }, deadline: Date.now() + 30000 }), /whole-number quantity/);
});

test('Depop worldwide shipping accepts free or paid shipping and verifies clearing', async () => {
  const { c } = newDraft();
  withCatalog(c);
  for (const price of [0, 15.25]) {
    const item = { ...FULL, depop_specifics: { ...FULL.depop_specifics, worldwideShipping: { enabled: true, price } } };
    const fields = await c.depopResolvedFields({ listing: item, deadline: Date.now() + 30000 });
    assert.equal(fields.international_shipping_cost, price.toFixed(2));
    const draft = { ...fields, description: c.depopDescription(item), price_amount: String(item.price) };
    assert.ok(!c.depopDraftMismatch(draft, item, fields).includes('worldwide shipping'));
    assert.ok(c.depopDraftMismatch({ ...draft, international_shipping_cost: null }, item, fields).includes('worldwide shipping'));
  }
  const off = await c.depopResolvedFields({ listing: FULL, deadline: Date.now() + 30000 });
  assert.equal(off.international_shipping_cost, null);
  assert.ok(c.depopDraftMismatch({ international_shipping_cost: '10.00' }, FULL, off).includes('worldwide shipping'));
  for (const price of ['', -1, 100, 'bad', 1.111]) {
    const item = { ...FULL, depop_specifics: { ...FULL.depop_specifics, worldwideShipping: { enabled: true, price } } };
    await assert.rejects(c.depopResolvedFields({ listing: item, deadline: Date.now() + 30000 }), /worldwide shipping price/);
  }
});

// Answers Depop's catalog and the seller's shipping reads; other requests go on.
function withCatalog(c, { attributes = [200, ATTRIBUTES], addresses = [200, [{ id: 41, postal_code: '70115' }, { id: 42, postal_code: '10001' }]],
  providers = [200, [{ id: 'USPS', parcelSizes: [{ id: 'small', title: 'Extra small' }, { id: 'medium', title: 'Medium' }] }]] } = {}) {
  const original = c.fetch;
  const reads = [];
  c.fetch = async (url, options = {}) => {
    const { hostname, pathname } = new URL(url);
    if (hostname === 'api.depop.com') throw new Error(`Depop partner host must not be called: ${url}`);
    if (hostname === 'webapi.depop.com' && pathname === '/presentation/api/v1/attributes/') {
      reads.push('attributes');
      const [status, body] = attributes;
      if (status !== 200 || !body || typeof body !== 'object') return reply(status, body);
      const { category_size_mapping, size_sets, ...rest } = body;
      return reply(200, rest);
    }
    if (hostname === 'webapi.depop.com' && pathname === '/presentation/api/v1/attributes/categories/size-mapping/') {
      reads.push('sizes');
      const [status, body] = attributes;
      if (status !== 200 || !body || typeof body !== 'object') return reply(status, body);
      return reply(200, { category_size_mapping: body.category_size_mapping, size_sets: body.size_sets });
    }
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
  let saved = { id: 123456, description: 'Private draft', price_amount: '12', price_currency: 'USD',
    pictures: [{ id: 789 }], missing_fields: ['brand'], shipping_methods: [] };
  c.fetch = async (url, options) => {
    requests.push({ url, ...options });
    if (options.method === 'PUT') saved = { ...saved, ...JSON.parse(options.body), pictures: JSON.parse(options.body).pictures.map(id => ({ id })) };
    return reply(200, saved);
  };
  return { c, requests, payload: { action: 'save_draft', draft_url: DRAFT, listing, deadline: Date.now() + 30000 } };
}

test('several Depop addresses at the ships-from ZIP use the one updated most recently', () => {
  const c = loadWorker();
  const data = [
    { id: 1, postalCode: '70115', lastModified: '2026-08-17T06:04:41Z' },
    { id: 2, postalCode: '70115', lastModified: '2026-08-23T16:17:37Z' },
    { id: 3, postalCode: '70125', lastModified: '2026-05-20T15:55:18Z' },
  ];
  assert.equal(call(c, 'depopAddress', { form: { ships_from_zip: '70115' } }, data).id, 2);
  assert.equal(call(c, 'depopAddress', { form: { ships_from_zip: '70125' } }, data).id, 3);
});

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

test('Depop keeps a draft whose read-back uses object quantities and picture ids', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  let saved = { id: 123456, description: 'Private draft', price_amount: '12', price_currency: 'USD', pictures: [] };
  const requests = [];
  c.fetch = async (url, options = {}) => {
    requests.push({ method: options.method, body: options.body });
    if (options.method === 'PUT') {
      const sent = JSON.parse(options.body);
      saved = { ...saved, ...sent, pictures: sent.pictures.map(id => ({ id })),
        variants: { 4: { quantity: 1 } }, attributes: [{ id: 'material', values: ['cotton'] }] };
    }
    return reply(200, saved);
  };
  const result = await c.depopApiAction('save', {
    action: 'save_draft', draft_url: DRAFT, listing, deadline: Date.now() + 30000,
    depop_fields: { product_type: 'jeans', variant_set: 54, variants: { 4: 1 }, attributes: { material: ['cotton'] } },
    uploaded_photo_ids: ['11', '12', '13', '14', '15', '16', '17', '18', '19'],
  });
  assert.equal(result.error, undefined);
  assert.equal(result.photo_count, 8);
  const body = JSON.parse(requests.find(request => request.method === 'PUT').body);
  assert.deepEqual(body.pictures, [11, 12, 13, 14, 15, 16, 17, 18]);
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
      ? { description: 'Private draft', price_amount: '12', pictures: [{}], missing_fields: [] } : { id: 'job-1' });
  };
  const result = await c.depopApiAction('publish', approved);
  assert.equal(result.publish_job_id, 'job-1');
  assert.deepEqual(requests.map(r => r.method), ['GET', 'POST']);
  assert.deepEqual(JSON.parse(requests[1].body), { draft_ids: ['123456'] });
});

test('a completed Depop publish returns the one public product link', async () => {
  const { c, payload } = page();
  c.fetch = async (url) => {
    const { pathname } = new URL(url);
    if (pathname.endsWith('/status/')) {
      return reply(200, { count_by_status: { COMPLETE: 1, PENDING: 0 },
        products: [{ slug: 'Seller-Columbia-XL', draft_id: '123456' }] });
    }
    throw new Error(pathname);
  };
  const status = await c.depopApiAction('publish_status', { ...payload, publish_job_id: 'job-1' });
  assert.equal(status.listing_url, 'https://www.depop.com/products/seller-columbia-xl/');
  assert.equal(call(c, 'depopStatusUrl', { products: [{ slug: 'one' }, { slug: 'two' }] }, '123456'), '');
});

test('a completed Depop publish without a slug uses the one matching selling listing', async () => {
  const { c, requests, payload } = page();
  c.fetch = async (url) => {
    const { pathname } = new URL(url);
    requests.push(pathname);
    if (pathname.endsWith('/status/')) return reply(200, { count_by_status: { COMPLETE: 1, PENDING: 0 } });
    if (pathname === '/presentation/api/v1/users/me/') return reply(200, { id: 42 });
    if (pathname.includes('/by-status/selling/')) {
      return reply(200, { objects: [
        { slug: 'other-item', description: 'Private draft', price_amount: '9' },
        { slug: 'seller-columbia-xl', description: 'Private draft\n\n#vintage', price_amount: '12.00' },
      ] });
    }
    throw new Error(pathname);
  };
  const status = await c.depopApiAction('publish_status', { ...payload, publish_job_id: 'job-1' });
  assert.equal(status.listing_url, 'https://www.depop.com/products/seller-columbia-xl/');
  assert.ok(requests.some(path => path.includes('/shops/42/products/by-status/selling/')));
});

test('a Depop inventory page names the posted product when the selling list does not', async () => {
  const { c, requests, payload } = page();
  const product = { slug: 'seller-columbia-xl-18-20-outdoors-hooded-ab12', description: 'Columbia XL Outdoors Hooded\n\nPrivate draft', price_amount: '12.00', user_id: 42 };
  c.fetch = async (url) => {
    const { pathname, search } = new URL(url);
    requests.push(`${pathname}${search}`);
    if (pathname.endsWith('/status/')) return reply(200, { count_by_status: { COMPLETE: 1, PENDING: 0 } });
    if (pathname === '/presentation/api/v1/users/me/') return reply(200, { id: 42 });
    if (pathname.includes('/by-status/selling/')) return reply(200, { objects: [] });
    if (pathname === '/api/v1/shop/42/products/') {
      if (!search.includes('offset_id=')) return reply(200, { meta: { last_offset_id: 'next', end: false }, products: [{ slug: 'other', description: 'Something else', price_amount: '9' }] });
      return reply(200, { meta: { end: true }, products: [product] });
    }
    throw new Error(pathname);
  };
  const status = await c.depopApiAction('publish_status', { ...payload, publish_job_id: 'job-1', listing: { ...payload.listing, title: 'Columbia XL Outdoors Hooded' } });
  assert.equal(status.listing_url, 'https://www.depop.com/products/seller-columbia-xl-18-20-outdoors-hooded-ab12/');
  assert.ok(requests.some(path => path.startsWith('/api/v1/shop/42/products/?limit=200&offset_id=')));
});

test('two Depop inventory rows for one listing do not pick a link', async () => {
  const { c, payload } = page();
  const product = (slug, description) => ({ slug, description, price_amount: '12.00', user_id: 42 });
  c.fetch = async (url) => {
    const { pathname } = new URL(url);
    if (pathname.endsWith('/status/')) return reply(200, { count_by_status: { COMPLETE: 1, PENDING: 0 } });
    if (pathname === '/presentation/api/v1/users/me/') return reply(200, { id: 42 });
    if (pathname.includes('/by-status/selling/')) return reply(200, { objects: [] });
    if (pathname === '/api/v1/shop/42/products/') {
      const text = 'Columbia XL Outdoors Hooded\n\nPrivate draft';
      return reply(200, { meta: { end: true }, products: [product('seller-columbia-one', text), product('seller-columbia-two', `${text} extra`)] });
    }
    throw new Error(pathname);
  };
  const status = await c.depopApiAction('publish_status', { ...payload, publish_job_id: 'job-1', listing: { ...payload.listing, title: 'Columbia XL Outdoors Hooded' } });
  assert.equal(status.listing_url, undefined);
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
    return reply(200, { id: 555, description: 'Private draft', price_amount: '12', pictures: [] });
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
  const capped = 'x'.repeat(1000);
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

test('a direct Depop save writes the listing site\'s draft fields', async () => {
  const { c, requests, msg } = newDraft();
  msg.payload.listing = {
    ...FULL, brand: 'Columbia', department: 'Boys', size: 'XL', price: 17,
    direct_category_ids: { depop: 'kidswear_jackets' },
    depop_specifics: { parcelSize: 'Medium', brand: 'Columbia', material: ['Polyester'] },
    form: { ...FULL.form, ships_from_zip: '70115', specifics: { age: ['modern'], source: ['preloved'], style: ['casual'] },
      overrides: { condition: 'used_good', brand: 'Columbia', categoryV2: {
        id: 'kidswear_jackets', path: ['kidswear', 'kidswear_coats-jackets', 'kidswear_jackets'],
      } } },
  };
  const attrs = {
    ...ATTRIBUTES,
    group: [...ATTRIBUTES.group, { id: 'coats-jackets', department: ['kidswear'], product_types: [
      { id: 'jackets', attribute_ids: ['material'] }] }],
    category_size_mapping: [...ATTRIBUTES.category_size_mapping, {
      department: 'kidswear', group: 'coats-jackets', product_type: 'jackets', size_set_by_region: { US: 32 } }],
    brand: [{ id: 'columbia-sportswear', name: 'Columbia Sportswear' }, { id: 'other', name: 'Other' }],
  };
  let saved = { id: 555, description: '', price_amount: '17', pictures: [] };
  c.fetch = async (url, options = {}) => {
    const { hostname, pathname } = new URL(url);
    requests.push({ method: options.method, hostname, pathname, body: options.body });
    if (hostname.endsWith('amazonaws.com')) return reply(200, '');
    if (options.method === 'POST' && pathname === '/presentation/api/v1/drafts/') return reply(200, { id: 555 });
    if (options.method === 'POST' && pathname === '/presentation/api/v1/pictures/') {
      return reply(200, { id: 9, url: 'https://media-photos-depop.s3-accelerate.amazonaws.com/p/9.jpg?X-Amz-Signature=private' });
    }
    if (pathname === '/presentation/api/v1/users/me/') return reply(200, { country: 'US' });
    if (pathname.endsWith('/countries.json')) return reply(200, [{ country: 'United States', countryCode: 'US', address: 'United States', geoLat: 39.3, geoLng: -97.9 }]);
    if (pathname === '/presentation/api/v1/users/me/settings/shipping/') return reply(200, { shipping_providers: [{ id: 'USPS', active: true }] });
    if (pathname === '/presentation/api/v1/addresses/') return reply(200, [{ id: 2, last_modified: '2024-01-01T00:00:00Z' }]);
    if (pathname === '/presentation/api/v1/shipping/providers/depop/') {
      return reply(200, [{ id: 'USPS', parcel_sizes: [{ id: 'under_1lb', title: 'Medium', cost: { amount: '1.99' } }] }]);
    }
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      saved = { ...saved, ...body, pictures: (body.pictures || []).map(id => ({ id })) };
    }
    return reply(200, saved);
  };
  withCatalog(c, { attributes: [200, attrs] });
  const result = await c.runDepopNative(msg);
  assert.equal(result.ok, true, result.error);
  const put = requests.find(request => request.method === 'PUT' && request.pathname.includes('/drafts/'));
  const body = JSON.parse(put.body);
  assert.equal(body.department, undefined);
  assert.equal(body.group, undefined);
  assert.equal(body.product_type, 'jackets');
  assert.equal(body.is_kids, true);
  assert.equal(body.gender, 'male');
  assert.equal(body.variant_set, 32);
  assert.deepEqual(body.variants, { 15: 1 });
  assert.equal(body.address, 'United States');
  assert.deepEqual(body.shipping_methods, [{
    payer: 'buyer', parcel_size: 'medium', shipping_provider_id: 'USPS', ship_from_address_id: 41,
  }]);
  assert.equal(body.shipping_methods[0].parcel_size_id, undefined);
});

test('a replaced Depop draft that Depop then refuses keeps the new link', async () => {
  const gone = 'https://www.depop.com/sellinghub/drafts/edit/123456/';
  const created = 'https://www.depop.com/sellinghub/drafts/edit/newdraft1/';
  const { c, msg } = newDraft();
  msg.payload.draft_url = gone;
  c.fetch = async (url, options = {}) => {
    const { hostname, pathname } = new URL(url);
    if (hostname.endsWith('amazonaws.com')) return reply(200, '');
    if (pathname === '/presentation/api/v1/drafts/123456/' || pathname === '/presentation/api/v1/drafts/123456') {
      return reply(404, { message: 'Could not find draft with id: 123456' });
    }
    if (options.method === 'POST' && pathname === '/presentation/api/v1/drafts/') return reply(201, { id: 'newdraft1' });
    if (options.method === 'POST' && pathname === '/presentation/api/v1/pictures/') {
      return reply(200, { id: 9, url: 'https://media-photos-depop.s3-accelerate.amazonaws.com/p/9.jpg?X-Amz-Signature=private' });
    }
    if (options.method === 'PUT') return reply(400, 'parcel_size is required', { 'content-type': 'text/plain' });
    return reply(200, { id: 'newdraft1', description: 'Private draft', price_amount: '12', pictures: [] });
  };
  withCatalog(c);
  const result = await c.runDepopNative(msg);
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.draft_replaced, true);
  assert.equal(result.draft_url, created);
  assert.match(result.error, /HTTP 400/);
  assert.match(result.marketplace_message, /parcel_size is required/);
});

test('a Depop save confirms the draft by reading it again when the first read fails', async () => {
  const { c, msg } = newDraft();
  msg.payload.listing = { ...FULL, tags: [] };
  msg.payload.draft_url = DRAFT;
  let saved = false;
  let kept = {};
  let readsAfterSave = 0;
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    if (options.method === 'PUT' && /\/drafts\/[^/]+\/$/.test(pathname)) {
      saved = true;
      kept = JSON.parse(options.body);
      return reply(200, {});
    }
    if ((!options.method || options.method === 'GET') && /\/drafts\/[^/]+\/$/.test(pathname)) {
      if (saved) {
        readsAfterSave += 1;
        // The read is retried on its own. Only a read after those retries is the confirmation.
        if (readsAfterSave <= 4) throw new Error('Disconnected');
      }
      return reply(200, { ...kept, variant_set_id: kept.variant_set, id: '123456', description: 'Private draft', price_amount: '12', pictures: [{ id: 789 }] });
    }
    return reply(200, {});
  };
  withCatalog(c);
  const result = await c.runDepopNative(msg);
  assert.equal(result.ok, true, result.error);
  assert.equal(result.state, 'draft');
  assert.equal(result.draft_url, DRAFT);
  assert.equal(readsAfterSave, 5);
});

test('a deleted Depop draft is replaced once and the missing link is not kept', async () => {
  const gone = 'https://www.depop.com/sellinghub/drafts/edit/123456/';
  const created = 'https://www.depop.com/sellinghub/drafts/edit/newdraft1/';
  const { c, requests, msg } = newDraft();
  msg.payload.draft_url = gone;
  let kept = { id: 'newdraft1', description: 'Private draft', price_amount: '12', pictures: [{ id: 9 }] };
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
    return reply(200, { description: 'Private draft', price_amount: '12', pictures: [{}], missing_fields: ['brand', 'parcel_size'],
      product_type: 'tshirts', variant_set_id: 12, variants: { 7: 1 }, attributes: { material: ['cotton'], occasion: ['casual'] } });
  };
  withCatalog(c);
  const result = await c.runDepopNative({ payload: { ...payload, listing: { ...FULL, tags: [] }, action: 'publish', confirm: true, operation_id: 'abc123' } });
  assert.equal(result.ok, false);
  assert.equal(result.error_code, 'missing_fields', result.error);
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
    if (options.method === 'PUT') return reply(200, { id: 123456, description: 'Private draft', price_amount: '12', pictures: [{ id: 789 }], missing_fields: [] });
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
    return reply(200, { id: 123456, description: 'Private draft', price_amount: '12', pictures: [{ id: 789 }], missing_fields: [] });
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

// Depop's kids coats, as its catalog lists them: an age size set and a jacket type.
const KIDS_JACKETS = {
  ...ATTRIBUTES,
  group: [...ATTRIBUTES.group, { id: 'coats-jackets', department: ['kidswear'], product_types: [
    { id: 'jackets', attribute_ids: ['material', 'occasion', 'jacket-type'] }] }],
  generic_attributes: [...ATTRIBUTES.generic_attributes, { id: 'jacket-type', max_selection: 2, is_mandatory: false,
    attributes: [{ id: 'puffer', name_i18n: { 'en-US': 'Puffer' } }, { id: 'windbreaker', name_i18n: { 'en-US': 'Windbreaker' } }] }],
  category_size_mapping: [...ATTRIBUTES.category_size_mapping,
    { department: 'kidswear', group: 'coats-jackets', product_type: 'jackets', size_set_by_region: { US: 100 } }],
  size_sets: [...ATTRIBUTES.size_sets, { id: 100, sizes: [{ id: 20, name_i18n: { 'en-US': '14 years' } },
    { id: 21, name_i18n: { 'en-US': 'Other' } }, { id: 23, name_i18n: { 'en-US': '16 years' } }] }],
};
const KIDS_JACKET = { ...listing, brand: 'Columbia', department: 'Boys', size: 'XL', condition: 'Pre-Owned - Good',
  direct_category_ids: { depop: 'kidswear_jackets' },
  depop_specifics: { size: '16 years', type: 'Windbreaker', material: ['Polyester'], parcelSize: 'Medium', location: 'New Orleans, LA, United States' },
  form: { overrides: { condition: 'used_good' }, specifics: {}, aspects: {}, ships_from_zip: '70115' } };

test('a kids jacket takes Depop\'s age size, its jacket type, and the ship-from city', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  withCatalog(c, { attributes: [200, KIDS_JACKETS], addresses: [200, [{ id: 41, postal_code: '70115', city: 'New Orleans', state: 'LA' }]] });
  const fields = await c.depopResolvedFields({ listing: KIDS_JACKET, deadline: Date.now() + 30000 });
  assert.equal(fields.variant_set, 100);
  assert.deepEqual(JSON.parse(JSON.stringify(fields.variants)), { 23: 1 });
  assert.deepEqual(JSON.parse(JSON.stringify(fields.attributes['jacket-type'])), ['windbreaker']);
  assert.equal(fields.address, 'New Orleans, LA');
  assert.equal(fields.geo_lat, undefined);
  // Other on the Depop form is never chosen for a tag size; the tag stays the fallback.
  const other = await c.depopResolvedFields({ listing: { ...KIDS_JACKET, size: '14 years',
    depop_specifics: { ...KIDS_JACKET.depop_specifics, size: 'Other' } }, deadline: Date.now() + 30000 });
  assert.deepEqual(JSON.parse(JSON.stringify(other.variants)), { 20: 1 });
  assert.deepEqual(JSON.parse(JSON.stringify(call(c, 'depopLocation', KIDS_JACKET, { id: 1, latitude: '29.95', longitude: '-90.07' }))),
    { address: 'New Orleans, LA, United States', geo_lat: 29.95, geo_lng: -90.07 });
});

test('Depop publish refuses a draft whose size or type is not the one sent', async () => {
  const { c, requests, payload } = page();
  const fields = { product_type: 'jackets', variant_set: 100, variants: { 23: 1 }, attributes: { 'jacket-type': ['windbreaker'] } };
  const draft = { description: 'Private draft', price_amount: '12', pictures: [{}], missing_fields: [],
    product_type: 'jackets', variant_set_id: 100, variants: { 21: 1 }, attributes: { 'jacket-type': ['windbreaker'] } };
  c.fetch = async (url, options) => {
    requests.push({ url, ...options });
    return reply(200, options.method === 'GET' ? draft : { id: 'job-1' });
  };
  const approved = { ...payload, action: 'publish', confirm: true, depop_fields: fields };
  const refused = await c.depopApiAction('publish', approved);
  assert.match(refused.error, /different size than this revision/);
  assert.deepEqual(requests.map(r => r.method || 'GET'), ['GET']);
  draft.variants = { 23: 1 };
  draft.attributes = { 'jacket-type': ['puffer'] };
  assert.match((await c.depopApiAction('publish', approved)).error, /different jacket type/);
  draft.attributes = { 'jacket-type': ['windbreaker'] };
  assert.equal((await c.depopApiAction('publish', approved)).publish_job_id, 'job-1');
});

test('a locked Depop price wins and nine photos stay within eight', () => {
  const c = loadWorker();
  assert.equal(call(c, 'depopPrice', {
    price: 29.99, depop_specifics: { price: 23 }, seller_fields: ['depop_specifics.price'],
  }), 23);
  assert.equal(call(c, 'depopPrice', { price: 29.99, depop_specifics: { price: 23 } }), 29.99);
  const photos = Array.from({ length: 9 }, (_, index) => ({ id: String(index + 1) }));
  const chosen = call(c, 'depopChosenPhotos', photos, {});
  assert.equal(chosen.photos.length, 8);
  assert.equal(chosen.omitted, 1);
  assert.deepEqual(call(c, 'depopPictureIds', Array.from({ length: 9 }, (_, index) => index + 1)), [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.deepEqual(call(c, 'depopMeasurementBody', {
    depop_specifics: { measurements: { unit: 'in', values: [
      { id: 'pit_to_pit', label: 'Pit to pit', value: '22', unit: 'in' },
      { id: 'length', label: 'Length', value: '  ', unit: 'in' },
    ] } },
  }), { measurements: [{ id: 'pit_to_pit', label: 'Pit to pit', value: '22', unit: 'in' }] });
});

test('a live Depop update sends the price and measurements by slug', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  const requests = [];
  c.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method, body: options.body });
    if (String(url).includes('/by-slug/') && options.method === 'PUT') {
      return reply(200, { slug: 'studio-works-fleece', price_amount: '29.99', price_currency: 'USD' });
    }
    if (String(url).includes('/by-slug/')) {
      return reply(200, { id: 99, slug: 'studio-works-fleece', price_amount: '23.00', price_currency: 'USD', pictures: [{ id: 7 }] });
    }
    return reply(200, { id: 99, price_amount: '29.99', price_currency: 'USD' });
  };
  const result = await c.depopApiAction('update', {
    action: 'update',
    confirm: true,
    listing_id: 'studio-works-fleece',
    listing: {
      description: 'Fleece',
      price: 29.99,
      depop_specifics: { measurements: { unit: 'in', values: [
        { id: 'pit_to_pit', label: 'Pit to pit', value: '22', unit: 'in' },
      ] } },
    },
    deadline: Date.now() + 30000,
  });
  assert.equal(result.ok, true);
  assert.equal(result.state, 'published');
  assert.match(result.listing_url, /\/products\/studio-works-fleece\/$/);
  assert.equal(result.previous_slug, 'studio-works-fleece');
  const put = requests.find((item) => item.method === 'PUT');
  assert.match(put.url, /\/presentation\/api\/v1\/products\/by-slug\/studio-works-fleece\/$/);
  const body = JSON.parse(put.body);
  assert.equal(body.price_amount, '29.99');
  assert.deepEqual(body.pictures, [7]);
  assert.deepEqual(body.measurements, [{ id: 'pit_to_pit', label: 'Pit to pit', value: '22', unit: 'in' }]);
  assert.equal(requests.some((item) => /\/products\/99\/$/.test(item.url)), false);
});

test('a live Depop update keeps every existing photo and leaves measurements unset when Anteroom has none', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  const pictures = Array.from({ length: 9 }, (_, index) => ({ id: index + 1 }));
  const requests = [];
  c.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method, body: options.body });
    if (options.method === 'PUT') return reply(200, { slug: 'studio-works-fleece', price_amount: '29.99' });
    return reply(200, { id: 99, slug: 'studio-works-fleece', price_amount: '23.00', price_currency: 'USD', pictures });
  };
  const update = listing => c.depopApiAction('update', {
    action: 'update', confirm: true, listing_id: 'studio-works-fleece', listing, deadline: Date.now() + 30000,
  });
  assert.equal((await update({ description: 'Fleece', price: 29.99 })).ok, true);
  const blank = await update({
    description: 'Fleece', price: 29.99,
    depop_specifics: { measurements: { unit: 'in', values: [{ id: 'pit_to_pit', label: 'Pit to pit', value: '  ', unit: 'in' }] } },
  });
  assert.equal(blank.ok, true);
  const bodies = requests.filter(item => item.method === 'PUT').map(item => JSON.parse(item.body));
  assert.equal(bodies.length, 2);
  for (const body of bodies) {
    assert.deepEqual(body.pictures, [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    assert.equal(Object.hasOwn(body, 'measurements'), false);
  }
});

test('a live Depop category change sends the new type and keeps the rewritten slug', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  const requests = [];
  c.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method, body: options.body });
    if (options.method === 'PUT') {
      return reply(200, { slug: 'vassarette-womens-tan-sweater', price_amount: '18.00', price_currency: 'USD' });
    }
    return reply(200, { id: 42, slug: 'vassarette-womens-tan-vest', price_amount: '18.00', price_currency: 'USD',
      pictures: [{ id: 3 }, { id: '4' }], product_type: 'vests' });
  };
  const result = await c.depopApiAction('update', {
    action: 'update', confirm: true, listing_id: 'vassarette-womens-tan-vest', deadline: Date.now() + 30000,
    depop_fields: { product_type: 'sweaters', gender: 'female', is_kids: false, attributes: { 'sweater-type': ['pullover'] },
      colour: ['tan'], brand: 'vassarette', condition: 'used_good' },
    listing: { description: 'Tube top', price: 18, primaryColor: 'Tan' },
  });
  assert.equal(result.ok, true, result.error);
  assert.equal(result.listing_url, 'https://www.depop.com/products/vassarette-womens-tan-sweater/');
  assert.equal(result.previous_slug, 'vassarette-womens-tan-vest');
  const body = JSON.parse(requests.find((item) => item.method === 'PUT').body);
  assert.equal(body.product_type, 'sweaters');
  assert.equal(body.gender, 'female');
  assert.deepEqual(body.attributes, { 'sweater-type': ['pullover'] });
  assert.deepEqual(body.colour, ['tan']);
  assert.deepEqual(body.pictures, [3, 4]);
  assert.equal(body.product_type === 'vests', false);
});

test('Depop treats object quantities and attribute lists as the fields it kept', () => {
  const c = loadWorker();
  const fields = { product_type: 'jeans', variant_set: 54, variants: { 4: 1 }, attributes: { material: ['cotton'] }, brand: 'anne-klein' };
  const draft = { description: 'Private draft', price_amount: '12', product_type: 'jeans', variant_set_id: 54,
    variants: { 4: { quantity: 1 } }, attributes: [{ id: 'material', values: ['cotton'] }], brand: 'anne-klein',
    pictures: [{ id: 1 }, { id: 2 }] };
  assert.deepEqual(call(c, 'depopDraftMismatch', draft, listing, fields), []);
  assert.deepEqual(call(c, 'depopPictureIds', draft), [1, 2]);
});

test('moving a Depop listing to crop tops replaces the vest type and drops its attributes', async () => {
  const c = loadWorker();
  const attributes = { ...ATTRIBUTES, group: ATTRIBUTES.group.map(group => group.id !== 'tops' ? group : {
    ...group, product_types: [...group.product_types,
      { id: 'vests', attribute_ids: ['occasion'] },
      { id: 'crop-tops', attribute_ids: ['material'] }],
  }) };
  withCatalog(c, { attributes: [200, attributes] });
  const fields = await c.depopLiveEditFields({
    description: 'Tube top', price: 18, brand: 'Vassarette', primaryColor: 'Tan', department: 'Women',
    direct_category_ids: { depop: 'womenswear_crop-tops' },
    depop_specifics: { material: ['Cotton'], attributes: { occasion: ['party'] } },
  }, Date.now() + 30000);
  const kept = JSON.parse(JSON.stringify(fields));
  assert.equal(kept.product_type, 'crop-tops');
  assert.equal(kept.gender, 'female');
  assert.deepEqual(kept.colour, ['tan']);
  assert.deepEqual(kept.attributes, { material: ['cotton'] });
  assert.equal(kept.brand, undefined);
});

test('Depop reuses one matching draft and does not pick the newest when several match', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  const logs = [];
  c.console.log = (...args) => logs.push(args.join(' '));
  const drafts = { objects: [
    { id: 'draft-b', description: 'Private draft', price_amount: '12', createdAt: '2026-10-26T18:00:00Z' },
    { id: 'draft-a', description: 'Private draft', price_amount: '12', createdAt: '2026-10-26T01:00:00Z' },
    { id: 'other', description: 'Something else', price_amount: '12', createdAt: '2026-10-26T19:00:00Z' },
  ] };
  c.fetch = async () => reply(200, drafts);
  const reused = await c.depopApiAction('reuse', { action: 'save_draft', listing, deadline: Date.now() + 30000 });
  assert.equal(reused.draft_url, 'https://www.depop.com/sellinghub/drafts/edit/draft-a/');
  assert.match(logs.join('\n'), /\[DEPOP\].*step=reuse.*matches=2.*draft=draft-a/);
  const stored = await c.depopApiAction('reuse', {
    action: 'save_draft', listing, deadline: Date.now() + 30000,
    draft_url: 'https://www.depop.com/sellinghub/drafts/edit/draft-b/',
  });
  assert.equal(stored.draft_url, 'https://www.depop.com/sellinghub/drafts/edit/draft-b/');
  drafts.objects = [{ id: 'only-draft', description: 'Private draft', price_amount: '12', createdAt: '2026-10-26T01:00:00Z' }];
  const only = await c.depopApiAction('reuse', { action: 'save_draft', listing, deadline: Date.now() + 30000 });
  assert.equal(only.draft_url, 'https://www.depop.com/sellinghub/drafts/edit/only-draft/');
});

test('reusing a Depop draft writes the full body and accepts Depop\'s read-back shape', async () => {
  const { c, requests, msg } = newDraft();
  const item = { ...FULL, tags: [], depop_specifics: { ...FULL.depop_specifics, age: ['modern'], source: ['preloved'], sku: '' } };
  msg.payload.listing = item;
  msg.payload.draft_url = DRAFT;
  let wrote = false;
  const stale = {
    id: '123456', description: 'Private draft', price_amount: '12', price_currency: 'USD', pictures: [],
    brand: { id: 'old-brand', name: 'Old Brand' }, videos: [{ id: 1, url: 'https://video.example/stale' }],
    repop_metadata: { source: 'stale' }, product_type: 'jeans',
    attributes: [{ id: 'material', values: ['polyester'] }],
    condition: { id: 'brand_new', name: 'Brand new' }, colour: ['black'],
    shipping_methods: [{ payer: 'buyer', parcel_size_id: 'stale', shipping_provider_id: 'USPS', ship_from_address_id: 9 }],
  };
  const kept = {
    id: '123456', description: 'Private draft', priceAmount: '12.00', productType: 'tshirts', variantSetId: 12,
    variants: { 7: { quantity: 1 } },
    attributes: [
      { id: 'material', values: [{ id: 'cotton', name: 'Cotton' }] },
      { id: 'occasion', values: [{ id: 'casual', name: 'Casual' }] },
    ],
    age: [{ id: 'modern', name: 'Modern' }], source: [{ id: 'preloved', name: 'Preloved' }],
    style: [{ id: 'casual', name: 'Casual' }], colour: [{ id: 'multi', name: 'Multi' }],
    condition: { id: 'used_good', name: 'Used - Good' }, brand: { id: 'nike', name: 'Nike' },
    pictures: [{ id: 9, formats: { P0: { url: 'https://media-photos.depop.com/b0.jpg', height: 1280, width: 1280 } } }],
    sku: '',
  };
  c.fetch = async (url, options = {}) => {
    const { hostname, pathname } = new URL(url);
    requests.push({ method: options.method, hostname, pathname, body: options.body });
    if (hostname.endsWith('amazonaws.com')) return reply(200, '');
    if (options.method === 'POST' && pathname === '/presentation/api/v1/pictures/') {
      return reply(200, { id: 9, url: 'https://media-photos-depop.s3-accelerate.amazonaws.com/p/9.jpg?X-Amz-Signature=private' });
    }
    if (options.method === 'PUT' && /\/drafts\/[^/]+\/$/.test(pathname)) {
      wrote = true;
      return reply(200, {});
    }
    if (pathname === '/presentation/api/v1/drafts/123456/') return reply(200, wrote ? kept : stale);
    return reply(200, {});
  };
  withCatalog(c);
  const result = await c.runDepopNative(msg);
  assert.equal(result.ok, true, result.error);
  const put = requests.find(request => request.method === 'PUT' && request.pathname.includes('/drafts/'));
  const body = JSON.parse(put.body);
  assert.equal(body.videos, undefined);
  assert.equal(body.repop_metadata, undefined);
  assert.equal(typeof body.brand, 'string');
  assert.equal(body.brand, 'nike');
  assert.equal(body.product_type, 'tshirts');
  assert.deepEqual(body.pictures, [9]);
  assert.equal(body.condition, 'used_good');
  assert.equal(body.shipping_methods[0].parcel_size_id, undefined);
  const fields = await c.depopResolvedFields({ listing: item, deadline: Date.now() + 30000 });
  const written = JSON.parse(JSON.stringify(c.depopDraftWriteBody(item, fields, [9])));
  assert.deepEqual(body, written);
  const created = JSON.parse(JSON.stringify(c.depopDraftWriteBody(item, fields, [])));
  assert.deepEqual(created.pictures, []);
  assert.equal(created.product_type, body.product_type);
  assert.deepEqual(created.attributes, body.attributes);
  assert.equal(created.brand, body.brand);
  assert.deepEqual(JSON.parse(JSON.stringify(call(c, 'depopDraftMismatch', kept, item, fields))), []);
  assert.equal(call(c, 'depopPictureCount', kept), 1);
});
