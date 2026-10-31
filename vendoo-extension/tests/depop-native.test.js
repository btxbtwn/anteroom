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
  direct_category_ids: { depop: 'menswear_tshirts' },
  depop_specifics: { parcelSize: 'Medium', material: ['Cotton'], occasion: ['Casual', 'Party'] },
  form: { overrides: { condition: 'used_good', brand: 'Nike', categoryV2: { id: 'menswear_tshirts', path: ['menswear', 'menswear_tops', 'menswear_tshirts'] } },
    specifics: { style: ['casual'] }, aspects: {}, ships_from_zip: '70115' } };

test('Depop reads its category list with the seller session', async () => {
  const { c } = newDraft();
  const seen = [];
  const original = c.fetch;
  c.fetch = async (url, options = {}) => {
    if (String(url).includes('/presentation/api/v1/attributes')) seen.push(options.headers || {});
    return original(url, options);
  };
  await c.depopAttributes(Date.now() + 30000);
  assert.ok(seen.length >= 2);
  assert.ok(seen.every(headers => headers.Authorization === 'Bearer private-session'));
  assert.ok(seen.every(headers => headers.Accept === 'application/json'));
});

test('Depop catalog asks for sign-in before reading when the session is missing', async () => {
  const { c } = newDraft();
  c.chrome.cookies.get = async () => null;
  c.fetch = async () => assert.fail('No catalog read without a seller session');
  await assert.rejects(c.depopAttributes(Date.now() + 30000), error =>
    error.code === 'signed_out' && /Sign in to Depop/.test(error.message));
});

test('Depop sends and verifies an exact SKU and quantity, and leaves a blank SKU off the draft', async () => {
  const { c } = newDraft();
  withCatalog(c);
  const blank = { ...FULL, quantity: 3, sku: 'item-details', depop_specifics: { ...FULL.depop_specifics, sku: '' } };
  const unset = await c.depopResolvedFields({ listing: blank, deadline: Date.now() + 30000 });
  assert.equal(Object.hasOwn(unset, 'sku'), false);
  assert.equal(Object.hasOwn(c.depopDraftWriteBody(blank, unset, [9]), 'sku'), false);
  assert.ok(!c.depopDraftMismatch({ sku: 'old-sku' }, blank, unset).includes('SKU'));
  const item = { ...FULL, quantity: 3, sku: 'item-details', depop_specifics: { ...FULL.depop_specifics, sku: ' shelf-2 ' } };
  const fields = await c.depopResolvedFields({ listing: item, deadline: Date.now() + 30000 });
  assert.equal(fields.sku, 'shelf-2');
  assert.equal(c.depopDraftWriteBody(item, fields, [9]).sku, 'shelf-2');
  assert.deepEqual(JSON.parse(JSON.stringify(Object.values(fields.variants))), [3]);
  const draft = { ...fields, description: c.depopDescription(item), price_amount: String(item.price), variant_set_id: fields.variant_set };
  assert.deepEqual(Array.from(c.depopDraftMismatch(draft, item, fields)), []);
  const size = Object.keys(fields.variants)[0];
  assert.ok(c.depopDraftMismatch({ ...draft, variants: { [size]: 1 } }, item, fields).includes('quantity'));
  assert.ok(c.depopDraftMismatch({ ...draft, sku: 'old-sku' }, item, fields).includes('SKU'));
  await assert.rejects(c.depopResolvedFields({ listing: { ...item, quantity: 0 }, deadline: Date.now() + 30000 }), /whole-number quantity/);
});

test('Depop posts the parcel id the sell page uses for that title', async () => {
  const { c } = newDraft();
  withCatalog(c, { providers: [200, [{ id: 'USPS', parcel_sizes: [
    { id: 'under_12oz', title: 'Small', subtitle: 'Under 12oz' },
    { id: 'under_1lb', title: 'Medium', subtitle: 'Under 1lb', suggested: true },
    { id: 'medium', title: 'Large', subtitle: 'Under 2lb' },
  ] }]] });
  const seen = [];
  const original = c.fetch;
  c.fetch = async (url, options = {}) => { seen.push(String(url)); return original(url, options); };
  const fields = await c.depopResolvedFields({ listing: FULL, deadline: Date.now() + 30000 });
  assert.equal(fields.shipping_methods[0].parcel_size, 'under_1lb');
  const quote = new URL(seen.find(url => url.includes('/shipping/providers/depop/')));
  assert.equal(quote.searchParams.get('group'), 'tops');
  assert.equal(quote.searchParams.get('product_type'), 'tshirts');
  assert.equal(quote.searchParams.get('gender'), 'male');
  seen.length = 0;
  const suggested = await c.depopResolvedFields({
    listing: { ...FULL, depop_specifics: { material: ['Cotton'], occasion: ['Casual'] } },
    deadline: Date.now() + 30000,
  });
  assert.equal(suggested.shipping_methods[0].parcel_size, 'under_1lb');
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
    if (pathname === '/api/v1/shipping-providers/' || pathname === '/presentation/api/v1/shipping/providers/depop/') {
      reads.push('providers'); return reply(...providers);
    }
    return original(url, options);
  };
  return reads;
}

// Depop's photo check: every uploaded id is valid and processed, unless told otherwise.
function withPictureCheck(c, valid = ids => ids) {
  const original = c.fetch;
  const checks = [];
  c.fetch = async (url, options = {}) => {
    if (new URL(url).pathname === '/presentation/api/v1/pictures/validate/') {
      const ids = JSON.parse(options.body).picture_ids;
      checks.push(ids);
      return reply(200, { valid_picture_ids: valid(ids), status: 'complete' });
    }
    return original(url, options);
  };
  return checks;
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
  withPictureCheck(c);
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

test('a Depop publish that has not started yet is waited for, not called a refusal', async () => {
  const { c, requests, payload } = page();
  c.chrome.storage.local.get = async () => ({});
  c.marketplaceSleep = async () => {};
  const sent = { ...FULL, tags: [] };
  const draft = {};
  let polls = 0;
  c.fetch = async (url, options = {}) => {
    requests.push({ url, ...options });
    const { pathname } = new URL(url);
    if (pathname.endsWith('/status/')) {
      polls += 1;
      // Depop accepts the job first and fills its counts in a few moments later.
      return reply(200, polls < 4 ? { count_by_status: {} }
        : { count_by_status: { COMPLETE: 1, PENDING: 0 }, products: [{ slug: 'seller-columbia-xl', draft_id: '123456' }] });
    }
    if (options.method === 'POST' && pathname === '/presentation/api/v1/drafts/products/') return reply(200, { id: 'job-1' });
    return reply(200, draft);
  };
  withCatalog(c);
  const fields = await c.depopResolvedFields({ listing: sent, deadline: Date.now() + 30000 });
  Object.assign(draft, call(c, 'depopDraftWriteBody', sent, fields, [789]), { pictures: [{ id: 789 }], missing_fields: [] });
  const result = await c.runDepopNative({ payload: { ...payload, listing: sent, action: 'publish', confirm: true, operation_id: 'abc123' } });
  assert.equal(result.ok, true, result.error);
  assert.equal(result.state, 'published');
  assert.equal(result.listing_url, 'https://www.depop.com/products/seller-columbia-xl/');
  assert.equal(polls, 4);
  assert.equal(requests.filter(request => request.method === 'POST'
    && new URL(request.url).pathname === '/presentation/api/v1/drafts/products/').length, 1);
});

test('a Depop publish job that ended without posting names its reason and leaves the draft to retry', async () => {
  const { c, requests, payload } = page();
  c.chrome.storage.local.get = async () => ({});
  c.marketplaceSleep = async () => {};
  const sent = { ...FULL, tags: [] };
  const draft = {};
  c.fetch = async (url, options = {}) => {
    requests.push({ url, ...options });
    const { pathname } = new URL(url);
    if (pathname.endsWith('/status/')) {
      return reply(200, { count_by_status: { COMPLETE: 0, PENDING: 0, FAILED: 1 },
        errors: [{ message: 'This listing needs a shipping method.' }] });
    }
    if (options.method === 'POST' && pathname === '/presentation/api/v1/drafts/products/') return reply(200, { id: 'job-1' });
    return reply(200, draft);
  };
  withCatalog(c);
  const fields = await c.depopResolvedFields({ listing: sent, deadline: Date.now() + 30000 });
  Object.assign(draft, call(c, 'depopDraftWriteBody', sent, fields, [789]), { pictures: [{ id: 789 }], missing_fields: [] });
  const result = await c.runDepopNative({ payload: { ...payload, listing: sent, action: 'publish', confirm: true, operation_id: 'abc123' } });
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, true);
  assert.equal(result.error_code, 'rejected');
  assert.match(result.error, /Depop could not publish this listing\. This listing needs a shipping method\./);
  assert.equal(result.draft_url, DRAFT);
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
    assert.match(result.marketplace_message, new RegExp(own));
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
  withPictureCheck(c);
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
  withPictureCheck(c);
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

test('a Depop draft that drops its package size is refused, not left to fail at publish', async () => {
  const { c, payload } = page();
  c.chrome.storage.local.get = async () => ({});
  c.marketplaceSleep = async () => {};
  const sent = { ...FULL, tags: [], form: { ...FULL.form, ships_from_zip: '70115' } };
  const saved = {};
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    if (pathname === '/api/v1/addresses/') return reply(200, [{ id: 2, postal_code: '70115', last_modified: '2024-01-01T00:00:00Z' }]);
    if (pathname === '/presentation/api/v1/users/me/') return reply(200, { country: 'US' });
    if (pathname.endsWith('/countries.json')) {
      return reply(200, [{ country: 'United States', countryCode: 'US', address: 'United States', geoLat: 39.3, geoLng: -97.9 }]);
    }
    if (pathname === '/presentation/api/v1/shipping/providers/depop/') {
      return reply(200, [{ id: 'USPS', parcel_sizes: [{ id: 'under_1lb', title: 'Medium', cost: { amount: '1.99' } }] }]);
    }
    return reply(200, saved);
  };
  withCatalog(c);
  const fields = await c.depopResolvedFields({ listing: sent, deadline: Date.now() + 30000 });
  assert.equal(fields.shipping_methods.length, 1);
  Object.assign(saved, call(c, 'depopDraftWriteBody', sent, fields, [789]),
    // Depop saved the draft without the package size the revision sent.
    { pictures: [{ id: 789 }], missing_fields: [], shipping_methods: [] });
  const result = await c.runDepopNative({ payload: { ...payload, listing: sent, action: 'save_draft', operation_id: 'abc123' } });
  assert.equal(result.ok, false);
  assert.match(result.error, /shipping/);
});

// A saved draft as Depop holds it, with the catalog and shipping reads a save makes.
function skuDraft({ stored = {} } = {}) {
  const c = loadWorker();
  c.AbortSignal = AbortSignal;
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  c.chrome.storage.local.get = async () => ({});
  c.marketplaceSleep = async () => {};
  const sent = { ...FULL, tags: [], sku: 'TOPSHOP-6', form: { ...FULL.form, ships_from_zip: '70115' } };
  const saved = { id: '123456', description: 'Private draft', price_amount: '12', pictures: [{ id: 789 }], missing_fields: [], ...stored };
  let writes = 0;
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    if (options.method === 'PUT') {
      writes += 1;
      const body = JSON.parse(options.body);
      // Depop takes a SKU once: another listing already holds this one.
      if (body.sku) return reply(400, { detail: 'SKU is invalid: SKU must be unique' });
      Object.assign(saved, body, { pictures: (body.pictures || []).map(id => ({ id })) });
      return reply(200, saved);
    }
    return reply(200, saved);
  };
  withCatalog(c);
  const run = async () => c.runDepopNative({ payload: { action: 'save_draft', draft_url: DRAFT, listing: sent, operation_id: 'abc123' } });
  return { c, run, writes: () => writes, sent };
}

test('a Depop draft a new SKU collides on is written again without it', async () => {
  const { c, run, writes, sent } = skuDraft();
  const result = await run();
  assert.equal(result.ok, true, result.error);
  assert.match(result.warning, /TOPSHOP-6/);
  assert.equal(writes(), 2);
  assert.equal(call(c, 'depopDraftWriteBody', sent, { sku: '' }, [789]).sku, undefined);
});

test('a Depop draft that already holds the SKU names what the seller has to clear', async () => {
  const { c, run } = skuDraft({ stored: { sku: 'TOPSHOP-6' } });
  const result = await run();
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, true);
  assert.match(result.error, /Clear this draft's SKU/);
});

test('a boys jacket draft sends Depop\'s category, brand, location, and parcel', async () => {
  const { c, requests, payload } = page();
  payload.listing = {
    ...listing, brand: 'Columbia', department: 'Boys', price: 12,
    depop_specifics: { categoryId: 'kidswear_jackets', parcelSize: 'Medium' },
    direct_category_ids: { depop: 'kidswear_jackets' },
    form: { ships_from_zip: '70115' },
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
    if (path === '/api/v1/addresses/' || path === '/presentation/api/v1/addresses/') {
      return reply(200, [
        { id: 1, postal_code: '70115', last_modified: '2020-01-01T00:00:00Z' },
        { id: 2, postal_code: '70115', last_modified: '2024-01-01T00:00:00Z' },
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
  assert.equal(new URL(quote.url).searchParams.get('product_type'), 'jackets');
  assert.equal(new URL(quote.url).searchParams.get('gender'), null);
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

test('Depop reads only its own stored category, never Vendoo\'s', () => {
  const c = loadWorker();
  const direct = call(c, 'depopCategory', {
    direct_category_ids: { depop: 'womenswear_dresses' },
    marketplace_category_ids: { depop: 'menswear_tshirts' },
    form: { overrides: { categoryV2: { id: 'menswear_tshirts', path: ['menswear', 'menswear_tops', 'menswear_tshirts'] } } },
  }, ATTRIBUTES);
  assert.equal(direct.productType, 'dresses');
  assert.equal(direct.department, 'womenswear');
  assert.equal(call(c, 'depopCategory', {
    marketplace_category_ids: { depop: 'menswear_tshirts' },
    marketplace_categories: { depop: 'Men > Tops > T-Shirts' },
    depop_specifics: { categoryId: 'menswear_tshirts' },
    form: { overrides: { categoryV2: { id: 'menswear_tshirts', path: ['menswear', 'menswear_tops', 'menswear_tshirts'] } } },
  }, ATTRIBUTES), null);
  assert.equal(call(c, 'depopCategoryFields', { marketplace_category_ids: { depop: 'menswear_tshirts' } }), null);
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
  // An explicit choice in Anteroom wins over the shared tag size, including Other.
  const other = await c.depopResolvedFields({ listing: { ...KIDS_JACKET, size: '14 years',
    depop_specifics: { ...KIDS_JACKET.depop_specifics, size: 'Other' } }, deadline: Date.now() + 30000 });
  assert.deepEqual(JSON.parse(JSON.stringify(other.variants)), { 21: 1 });
  assert.deepEqual(JSON.parse(JSON.stringify(call(c, 'depopLocation', KIDS_JACKET, { id: 1, latitude: '29.95', longitude: '-90.07' }))),
    { address: 'New Orleans, LA, United States', geo_lat: 29.95, geo_lng: -90.07 });
});

test('a men\'s waist keeps the tag size when Depop spells it with inches', () => {
  const c = loadWorker();
  const attributes = { ...ATTRIBUTES,
    category_size_mapping: [...ATTRIBUTES.category_size_mapping,
      { department: 'menswear', group: 'bottoms', product_type: 'jeans', size_set_by_region: { US: 40 } },
      { department: 'womenswear', group: 'bottoms', product_type: 'jeans', size_set_by_region: { US: 41 } }],
    size_sets: [...ATTRIBUTES.size_sets,
      { id: 40, sizes: [{ id: 70, name_i18n: { 'en-US': '36"' } }, { id: 71, name_i18n: { 'en-US': '38"' } }] },
      { id: 41, sizes: [{ id: 80, name_i18n: { 'en-US': '36' } }, { id: 81, name_i18n: { 'en-US': '36"' } }] }] };
  const category = { department: 'menswear', group: 'bottoms', productType: 'jeans', leaf: 'menswear_jeans', attributeIds: [] };
  const found = c.depopSize({ ...listing, size: '36', depop_specifics: {} }, category, attributes);
  assert.deepEqual([found.set, found.id], [40, 70]);
  // A list that offers the plain number and the inch size keeps the plain one.
  const women = { ...category, department: 'womenswear', leaf: 'womenswear_jeans' };
  const both = c.depopSize({ ...listing, size: '36', depop_specifics: {} }, women, attributes);
  assert.deepEqual([both.set, both.id], [41, 80]);
  // A size this list does not carry still needs the seller.
  assert.ok(c.depopSize({ ...listing, size: 'fits small', depop_specifics: {} }, category, attributes).problem);
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
  const chosen = call(c, 'depopChosenPhotos', photos);
  assert.equal(chosen.photos.length, 8);
  assert.equal(chosen.omitted, 1);
  assert.deepEqual(call(c, 'depopPictureIds', Array.from({ length: 9 }, (_, index) => index + 1)), [1, 2, 3, 4, 5, 6, 7, 8]);
});

// A live product read and edit by slug, with the seller's account reads a draft save also makes.
const LIVE = { ...FULL, tags: [], description: 'Fleece', price: 29.99 };
const NOLA = [200, [{ id: 41, postal_code: '70115', city: 'New Orleans', state: 'LA' }]];
// Depop's stored product after a PUT: by default it keeps what was sent.
const keepAll = (product, body) => ({ ...product, price_amount: body.price_amount, variant_set_id: body.variant_set,
  attributes: { ...product.attributes, product_type: body.product_type },
  variants_all: Object.keys(body.variants || {}).map(id => ({ id: Number(id), quantity: 1 })) });
function liveDepop({ product, put = () => reply(200, { slug: product.slug, price_amount: '29.99' }), attributes = ATTRIBUTES,
  stored = keepAll, read } = {}) {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  c.marketplaceSleep = async () => {};
  const requests = [];
  let current = product;
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    requests.push({ url: String(url), pathname, method: options.method, body: options.body });
    if (pathname.startsWith('/presentation/api/v1/products/by-slug/')) {
      if (options.method === 'PUT') {
        const body = JSON.parse(options.body);
        current = stored(current, body);
        return put(body);
      }
      return read ? read(pathname, current) : reply(200, current);
    }
    if (pathname === '/presentation/api/v1/users/me/') return reply(200, { id: 5, country: 'US' });
    if (pathname.endsWith('/countries.json')) return reply(200, [{ country: 'United States', countryCode: 'US', address: 'United States', geoLat: 39.3, geoLng: -97.9 }]);
    return reply(200, {});
  };
  withCatalog(c, { attributes: [200, attributes], addresses: NOLA });
  const update = listing => c.depopApiAction('update', {
    action: 'update', confirm: true, listing_id: product.slug, listing, deadline: Date.now() + 30000,
  });
  const puts = () => requests.filter(item => item.method === 'PUT').map(item => JSON.parse(item.body));
  return { c, requests, update, puts };
}

test('a live Depop update sends the body Depop\'s own edit page sends, by slug', async () => {
  const product = { id: 99, slug: 'studio-works-fleece', price_amount: '23.00', brand_id: 'nike', department: 'menswear',
    attributes: { group: 'tops', product_type: 'tshirts' },
    pictures: [{ id: 7, url: 'https://media-photos.depop.com/b0/7.jpg?X-Amz-Signature=abc' }, { id: 8 }] };
  const { update, puts, requests } = liveDepop({ product });
  const result = await update(LIVE);
  assert.equal(result.ok, true, `${result.error} ${result.marketplace_message || ''}`);
  assert.equal(result.listing_url, 'https://www.depop.com/products/studio-works-fleece/');
  const [body] = puts();
  assert.match(requests.find(item => item.method === 'PUT').url, /\/presentation\/api\/v1\/products\/by-slug\/studio-works-fleece\/$/);
  assert.deepEqual(body.picture_ids, [7, 8]);
  assert.equal(body.price_amount, '29.99');
  assert.equal(body.price_currency, 'USD');
  assert.equal(body.product_type, 'tshirts');
  assert.equal(body.gender, 'male');
  assert.equal(body.is_kids, false);
  assert.equal(body.variant_set, 12);
  assert.deepEqual(body.variants, { 7: 1 });
  assert.equal(body.brand, 'nike');
  assert.equal(body.condition, 'used_good');
  assert.deepEqual(body.colour, ['multi']);
  assert.deepEqual(body.attributes, { material: ['cotton'], occasion: ['casual'] });
  assert.equal(body.country, 'US');
  assert.equal(body.address, 'New Orleans, LA');
  assert.equal(body.geo_position_lat, 39.3);
  assert.equal(body.geo_position_lng, -97.9);
  assert.deepEqual(body.shipping_methods, [{ payer: 'buyer', parcel_size: 'medium', shipping_provider_id: 'USPS', ship_from_address_id: 41 }]);
  for (const key of ['pictures', 'pictureIds', 'department', 'size_set_id', 'size_id', 'measurements', 'geo_lat', 'geo_lng',
    'country_code', 'productType', 'priceAmount', 'variantSetId', 'brand_name', 'sku', 'id', 'slug', 'seller', 'status']) {
    assert.equal(Object.hasOwn(body, key), false, key);
  }
});

test('a live Depop move to crop tops sends the new type with its own size set and keeps the brand', async () => {
  const attributes = { ...ATTRIBUTES,
    group: ATTRIBUTES.group.map(group => group.id !== 'tops' ? group : { ...group, product_types: [...group.product_types,
      { id: 'vests-tanks-camis', attribute_ids: ['occasion'] }, { id: 'crop-top', attribute_ids: ['material'] }] }),
    category_size_mapping: [...ATTRIBUTES.category_size_mapping,
      { department: 'womenswear', group: 'tops', product_type: 'vests-tanks-camis', size_set_by_region: { US: 4 } },
      { department: 'womenswear', group: 'tops', product_type: 'crop-top', size_set_by_region: { US: 22 } }],
    size_sets: [...ATTRIBUTES.size_sets, { id: 4, sizes: [{ id: 40, name_i18n: { 'en-US': 'M' } }] }] };
  const product = { id: 42, slug: 'vassarette-tube-top', price_amount: '6.00', brand_id: 'vassarette',
    attributes: { group: 'tops', product_type: 'vests-tanks-camis' }, pictures: [{ id: 3 }, { id: '4' }] };
  const moved = { ...LIVE, description: 'Tube top', price: 6, brand: 'Vassarette', size: 'M', department: 'Women',
    direct_category_ids: { depop: 'womenswear_crop-top' }, depop_specifics: { parcelSize: 'Extra small', material: ['Cotton'] },
    form: { ...LIVE.form, overrides: { condition: 'used_good', brand: 'Vassarette' } } };
  const { update, puts } = liveDepop({ product, attributes,
    put: () => reply(200, { slug: 'vassarette-crop-top', price_amount: '6.00', attributes: { product_type: 'crop-top' } }) });
  const result = await update(moved);
  assert.equal(result.ok, true, `${result.error} ${result.marketplace_message || ''}`);
  assert.equal(result.listing_url, 'https://www.depop.com/products/vassarette-crop-top/');
  assert.equal(result.previous_slug, 'vassarette-tube-top');
  const [body] = puts();
  assert.equal(body.product_type, 'crop-top');
  assert.equal(body.variant_set, 22);
  assert.deepEqual(body.variants, { 4: 1 });
  assert.deepEqual(body.attributes, { material: ['cotton'] });
  assert.equal(body.brand, 'vassarette');
  assert.deepEqual(body.picture_ids, [3, 4]);
  assert.deepEqual(body.shipping_methods, [{ payer: 'buyer', parcel_size: 'small', shipping_provider_id: 'USPS', ship_from_address_id: 41 }]);

  // A size id chosen for the tank-top set is not in the crop-top set: nothing is sent.
  const stale = liveDepop({ product, attributes });
  const refused = await stale.update({ ...moved, depop_specifics: { ...moved.depop_specifics, nativeSizeId: 40 } });
  assert.equal(refused.ok, false);
  assert.equal(refused.safe_to_retry, true);
  assert.match(refused.error, /a size Depop offers for this category/);
  assert.deepEqual(stale.puts(), []);
});

test('a refused live Depop update keeps Depop\'s words and the field names sent, never an address or token', async () => {
  const product = { id: 99, slug: 'studio-works-fleece', price_amount: '23.00', pictures: [{ id: 7 }] };
  const text = liveDepop({ product, put: () => reply(400, 'Request failed with status code 400', { 'content-type': 'text/plain' }) });
  const plain = await text.update(LIVE);
  assert.match(plain.error, /HTTP 400/);
  assert.equal(plain.safe_to_retry, true);
  assert.match(plain.marketplace_message, /Request failed with status code 400/);
  assert.match(plain.marketplace_message, /sent: .*picture_ids/);
  assert.equal(plain.marketplace_message.includes('New Orleans'), false);
  const json = liveDepop({ product, put: () => reply(400, { message: 'Invalid product',
    errors: [{ field: 'variant_set', message: 'does not match product_type' }],
    address: { line_1: '3214 Louisiana Ave', postal_code: '70125', city: 'New Orleans' },
    authorization: 'Bearer abcdefghijklmnopqrstuvwxyz0123456789' }) });
  const detailed = await json.update(LIVE);
  assert.match(detailed.marketplace_message, /variant_set: does not match product_type/);
  for (const secret of ['3214 Louisiana', '70125', 'New Orleans', 'abcdefghijklmnopqrstuvwxyz0123456789']) {
    assert.equal(detailed.marketplace_message.includes(secret), false, secret);
  }
});

test('a live Depop update stops before writing when Depop names no photo ids or keeps the old category', async () => {
  const bare = liveDepop({ product: { id: 99, slug: 'studio-works-fleece', price_amount: '23.00', pictures: [] } });
  const none = await bare.update(LIVE);
  assert.equal(none.ok, false);
  assert.equal(none.safe_to_retry, true);
  assert.match(none.error, /photo ids/);
  assert.deepEqual(bare.puts(), []);
  const kept = liveDepop({ product: { id: 99, slug: 'studio-works-fleece', price_amount: '23.00', pictures: [{ id: 7 }] },
    stored: (product, body) => ({ ...keepAll(product, body), attributes: { product_type: 'jeans' } }) });
  const wrong = await kept.update(LIVE);
  assert.equal(wrong.ok, false);
  assert.equal(wrong.safe_to_retry, false);
  assert.match(wrong.error, /did not keep this listing's category/);
});

// The reply Depop sent job e457c63a3fce for the Vassarette move: the product
// as it stood before the edit, with no price field at all.
const VASSARETTE = { active_status: 'active', id: 971839814, slug: 'betwixtandbetweenco-vassarette-tube-top',
  attributes: { condition: 'used_good', colour: ['tan'], brand: 'vassarette', gender: 'female', is_kids: false, group: 'tops',
    product_type: 'vests-tanks-camis', additional_attributes: { material: ['nylon'] } },
  brand_id: 20239, brand_name: 'Vassarette', country: 'US', created_at: '2026-10-26T04:31:12.677151Z', description: 'Tube top',
  listed_quantity: 1, pictures: [{ id: 5049896356 }, { id: 5049895777 }], status: 'STATUS_ONSALE',
  updated_at: '2026-10-26T04:31:12.801757Z', user_id: 439456208, variant_set_id: 4,
  variants_all: [{ id: 17, quantity: 1, status: 'STATUS_ONSALE', variant: 'M' }] };
const CROP_ATTRIBUTES = { ...ATTRIBUTES,
  group: ATTRIBUTES.group.map(group => group.id !== 'tops' ? group : { ...group, product_types: [...group.product_types,
    { id: 'vests-tanks-camis', attribute_ids: ['material'] }, { id: 'crop-top', attribute_ids: ['material'] }] }),
  category_size_mapping: [...ATTRIBUTES.category_size_mapping,
    { department: 'womenswear', group: 'tops', product_type: 'vests-tanks-camis', size_set_by_region: { US: 4 } },
    { department: 'womenswear', group: 'tops', product_type: 'crop-top', size_set_by_region: { US: 4 } }],
  size_sets: [...ATTRIBUTES.size_sets, { id: 4, sizes: [{ id: 17, name_i18n: { 'en-US': 'M' } }] }],
  brand: [...ATTRIBUTES.brand, { id: 'vassarette', name: 'Vassarette' }] };
const CROP_MOVE = { ...LIVE, description: 'Tube top', price: 6, brand: 'Vassarette', size: 'M', department: 'Women',
  direct_category_ids: { depop: 'womenswear_crop-top' }, depop_specifics: { parcelSize: 'Extra small', material: ['Cotton'] },
  form: { ...LIVE.form, overrides: { condition: 'used_good', brand: 'Vassarette' } } };
// The owner's edit document carries the price; the public by-slug one does not.
const editListing = state => ({ ...state, price_amount: state.price_amount, product_type: state.attributes.product_type });
const publicRead = state => { const { price_amount: _price, ...rest } = state; return rest; };

test('a Depop price the document does not carry is unknown, not zero', () => {
  const c = loadWorker();
  assert.equal(call(c, 'depopProductPrice', VASSARETTE), null);
  assert.equal(call(c, 'depopProductPrice', { price: '' }), null);
  assert.equal(call(c, 'depopProductPrice', { price_amount: '6.00' }), 6);
  assert.equal(call(c, 'depopProductPrice', { price: { price_amount: '6.00' } }), 6);
  assert.equal(call(c, 'depopProductPrice', { pricing: { current_price: 0 } }), 0);
});

test('a live Depop update answered with the pre-edit product and no price checks a fresh read instead', async () => {
  const live = liveDepop({ product: { ...VASSARETTE, price_amount: undefined }, attributes: CROP_ATTRIBUTES,
    put: () => reply(200, VASSARETTE),
    read: (pathname, state) => reply(200, pathname.endsWith('/edit-listing/') ? editListing(state) : publicRead(state)) });
  const result = await live.update(CROP_MOVE);
  assert.equal(result.ok, true, `${result.error} ${result.marketplace_message || ''}`);
  assert.equal(result.price, 6);
  assert.equal(result.warning, undefined);
  const [body] = live.puts();
  assert.equal(body.product_type, 'crop-top');
  assert.equal(body.variant_set, 4);
  assert.deepEqual(body.variants, { 17: 1 });
  const reads = live.requests.filter(item => item.method === 'GET' && item.pathname.includes('/products/by-slug/'));
  assert.ok(reads.some(item => item.pathname.endsWith(`/by-slug/${VASSARETTE.slug}/edit-listing/`)));
});

test('a live Depop update waits out read-after-write lag before calling a field not kept', async () => {
  let reads = 0;
  const lagging = liveDepop({ product: VASSARETTE, attributes: CROP_ATTRIBUTES, put: () => reply(200, VASSARETTE),
    read: (pathname, state) => {
      if (pathname.endsWith('/edit-listing/')) return reply(404, { message: 'Not found' });
      reads += 1;
      // The read before the edit, then two stale reads after it.
      return reply(200, reads <= 3 ? publicRead(VASSARETTE) : publicRead(state));
    } });
  const result = await lagging.update(CROP_MOVE);
  assert.equal(result.ok, true, `${result.error} ${result.marketplace_message || ''}`);
  assert.equal(reads, 4);
  // The public read never names a price: the result says so instead of failing.
  assert.equal(result.price, null);
  assert.match(result.warning, /did not show the price/);
});

test('a live Depop update that Depop never applies names the category after every retry', async () => {
  const ignored = liveDepop({ product: VASSARETTE, attributes: CROP_ATTRIBUTES, put: () => reply(200, VASSARETTE),
    stored: product => product, read: (pathname, state) => reply(200, publicRead(state)) });
  const result = await ignored.update(CROP_MOVE);
  assert.equal(result.ok, false);
  assert.equal(result.safe_to_retry, false);
  assert.match(result.error, /did not keep this listing's category/);
  assert.doesNotMatch(result.error, /price/);
  assert.match(result.marketplace_message, /read back: price=\? product_type=vests-tanks-camis/);
  assert.match(result.marketplace_message, /wanted: price=6 product_type=crop-top/);
  const gets = ignored.requests.filter(item => item.method === 'GET' && item.pathname.includes('/products/by-slug/'));
  assert.equal(gets.length, 1 + 4 * 2);
  // A price a read does show and that differs still fails.
  const cheaper = liveDepop({ product: VASSARETTE, attributes: CROP_ATTRIBUTES, put: () => reply(200, VASSARETTE),
    stored: (product, body) => ({ ...keepAll(product, body), price_amount: '5.00' }) });
  const priced = await cheaper.update(CROP_MOVE);
  assert.match(priced.error, /did not keep this listing's price/);
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
  withPictureCheck(c);
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

test('Depop read-back understands a wrapped editor document and partner size ids', () => {
  const c = loadWorker();
  const fields = { product_type: 'tshirts', variant_set: 52, variants: { 10: 1 }, attributes: { material: ['cotton'] },
    brand: 'nike', colour: ['black'] };
  const wrapped = { description: 'Private draft', price: { priceAmount: '12.00' }, draft: {
    productType: 'tshirts', variantSetId: 52, variants: { 10: 1 }, brandId: 'nike', colour: ['black'],
    attributes: { material: ['cotton'] },
    media: { pictures: [{ id: 4, formats: { P0: { url: 'https://media-photos.depop.com/p.jpg?sig=abcdefghijklmnopqrstuvwxyz012345' } } }] },
  } };
  assert.deepEqual(call(c, 'depopDraftMismatch', wrapped, listing, fields), []);
  assert.deepEqual(call(c, 'depopPictureIds', wrapped), [4]);
  const partner = { description: 'Private draft', price_amount: '12', product_type: 'tshirts', size_set_id: 52, size_id: 10,
    quantity: 1, brand_name: 'nike', colour: ['black'], attributes: { material: ['cotton'] },
    pictures: [{ url: 'https://media-photos.depop.com/p.jpg' }] };
  assert.deepEqual(call(c, 'depopDraftMismatch', partner, listing, fields), []);
  assert.equal(call(c, 'depopPictureCount', partner), 1);
});

test('a reused Depop draft keeps its shipping and writes only the listing site\'s field names', () => {
  const c = loadWorker();
  const existing = {
    measurements: [{ id: 'pit_to_pit', label: 'Pit to pit', value: '22', unit: 'in' }],
    shippingMethods: [{ payer: 'buyer', parcelSize: 'small', shippingProviderId: 'USPS', shipFromAddressId: 3 }],
    nationalShippingCost: '4.50',
  };
  const item = { ...listing, depop_specifics: { measurements: { unit: 'in', values: [{ id: 'length', label: 'Length', value: '20', unit: 'in' }] } } };
  const body = JSON.parse(JSON.stringify(call(c, 'depopDraftWriteBody', item,
    { product_type: 'tshirts', variant_set: 12, variants: { 7: 1 }, gender: '', sku: '' }, [9], existing)));
  assert.equal(body.product_type, 'tshirts');
  assert.equal(body.variant_set, 12);
  assert.deepEqual(body.pictures, [9]);
  assert.deepEqual(body.shipping_methods, [{ payer: 'buyer', parcel_size: 'small', shipping_provider_id: 'USPS', ship_from_address_id: 3 }]);
  assert.equal(body.national_shipping_cost, '4.50');
  for (const key of ['productType', 'variantSetId', 'pictureIds', 'priceAmount', 'priceCurrency', 'shippingMethods',
    'nationalShippingCost', 'isKids', 'measurements', 'sku', 'gender']) {
    assert.equal(Object.hasOwn(body, key), false, key);
  }
});
test('a Depop draft gets its new photos only after Depop says they are processed', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  c.marketplaceSleep = async () => {};
  const fields = { product_type: 'tshirts', variant_set: 12, variants: { 7: 1 }, condition: 'used_good', brand: 'nike' };
  let saved = { id: '123456', description: 'Private draft', price_amount: '12', pictures: [] };
  const puts = [];
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      puts.push(body);
      saved = { ...saved, ...body, pictures: body.pictures.map(id => ({ id })) };
      return reply(200, {});
    }
    if (pathname === '/presentation/api/v1/drafts/123456/') return reply(200, saved);
    return reply(200, {});
  };
  const save = () => c.depopApiAction('save', {
    action: 'save_draft', draft_url: DRAFT, listing, deadline: Date.now() + 30000, depop_fields: fields, uploaded_photo_ids: [9, 10],
  });
  const slow = withPictureCheck(c, ids => ids.slice(0, 1));
  const waiting = await save();
  assert.equal(waiting.safe_to_retry, true);
  assert.match(waiting.error, /not finished processing 1 of the photos/);
  assert.equal(slow.length, 10);
  assert.deepEqual(puts, []);
  const ready = withPictureCheck(c);
  const result = await save();
  assert.equal(result.error, undefined, `${result.error} ${result.marketplace_message || ''}`);
  assert.deepEqual(JSON.parse(JSON.stringify(ready)), [[9, 10]]);
  assert.deepEqual(puts[0].pictures, [9, 10]);
  assert.deepEqual(JSON.parse(JSON.stringify(result.picture_ids)), [9, 10]);
});
test('Depop read-back uses the editor draft when the presentation document is only a summary', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  const fields = { product_type: 'tshirts', variant_set: 12, variants: { 7: 1 }, colour: ['black'], condition: 'used_good', brand: 'nike' };
  const editor = { id: '123456', description: 'Private draft', priceAmount: '12.00', productType: 'tshirts', variantSetId: 12,
    variants: { 7: 1 }, colour: ['black'], condition: 'used_good', brand: 'nike', pictures: [{ id: 9 }] };
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    if (options.method === 'PUT') return reply(200, {});
    if (pathname === '/api/v2/drafts/123456/') return reply(200, editor);
    if (pathname === '/presentation/api/v1/drafts/123456/') {
      return reply(200, { id: '123456', description: 'Private draft', price_amount: '12', pictures: [] });
    }
    return reply(200, {});
  };
  withPictureCheck(c);
  const result = await c.depopApiAction('save', {
    action: 'save_draft', draft_url: DRAFT, listing, deadline: Date.now() + 30000,
    depop_fields: fields, uploaded_photo_ids: [9], expected_photo_count: 1,
  });
  assert.equal(result.error, undefined, `${result.error} ${result.marketplace_message || ''}`);
  assert.equal(result.product_type, 'tshirts');
  assert.equal(result.photo_count, 1);
});

test('a Depop draft that did not stick reports the sanitized read-back', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  const fields = { product_type: 'tshirts', variant_set: 12, variants: { 7: 1 }, colour: ['black'], condition: 'used_good', brand: 'nike' };
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    if (options.method === 'PUT') return reply(200, { id: '123456', status: 'ignored', address: 'New Orleans, LA' });
    if (pathname === '/api/v2/drafts/123456/') return reply(404, { message: 'Could not find draft', token: 'private-response-token' });
    if (pathname === '/presentation/api/v1/drafts/123456/') {
      return reply(200, { id: '123456', description: 'Private draft', price_amount: '12', pictures: [],
        session: 'private-response-token', address: 'New Orleans, LA', geo_lat: 29.95 });
    }
    return reply(200, {});
  };
  withPictureCheck(c);
  const result = await c.depopApiAction('save', {
    action: 'save_draft', draft_url: DRAFT, listing, deadline: Date.now() + 30000,
    depop_fields: fields, uploaded_photo_ids: [9], expected_photo_count: 1,
  });
  assert.match(result.error, /did not keep this listing's category/);
  assert.match(result.error, /photos/);
  assert.match(result.marketplace_message, /read-back keys: description, price_amount/);
  assert.match(result.marketplace_message, /Private draft/);
  assert.equal(result.marketplace_message.includes('private-response-token'), false);
  assert.match(result.marketplace_message, /write reply: .*ignored/);
  assert.equal(result.marketplace_message.includes('New Orleans'), false);
  assert.equal(result.marketplace_message.includes('29.95'), false);
  assert.ok(result.marketplace_message.length > 80);
});


test('a parcel row with no quoted price still gives the draft a national shipping cost', () => {
  const c = loadWorker();
  // Depop's three real shapes, plus the ones that name no price at all.
  assert.equal(call(c, 'depopQuotedCost', { amount: '1.99' }), '1.99');
  assert.equal(call(c, 'depopQuotedCost', { price: { amount: '1.99' } }), '1.99');
  assert.equal(call(c, 'depopQuotedCost', 1.9), '1.90');
  assert.equal(call(c, 'depopQuotedCost', { amount: '0' }), '0.00');
  assert.equal(call(c, 'depopQuotedCost', 0), '0.00');
  assert.equal(call(c, 'depopQuotedCost', undefined), null);
  assert.equal(call(c, 'depopQuotedCost', null), null);
  assert.equal(call(c, 'depopQuotedCost', {}), null);
});

test('free shipping is written, not dropped as an empty value', () => {
  const c = loadWorker();
  const fields = { product_type: 'tshirts', national_shipping_cost: '0.00',
    shipping_methods: [{ payer: 'buyer', parcel_size: 'small', shipping_provider_id: 'USPS', ship_from_address_id: 2 }] };
  const body = JSON.parse(JSON.stringify(call(c, 'depopDraftWriteBody', listing, fields, [], null)));
  assert.equal(body.national_shipping_cost, '0.00');
  assert.equal(body.shipping_methods.length, 1);
  // A genuinely blank gender is still left out.
  const blank = JSON.parse(JSON.stringify(call(c, 'depopDraftWriteBody', listing, { gender: '' }, [], null)));
  assert.equal(Object.hasOwn(blank, 'gender'), false);
});

test('a draft whose only missing answer is shipping is written again before publish gives up', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  c.marketplaceSleep = async () => {};
  withPictureCheck(c);
  let draft = { id: '123456', description: 'Private draft', price_amount: '12', product_type: 'tshirts',
    pictures: [{ id: 9 }], missing_fields: ['national_shipping'] };
  const puts = [];
  c.fetch = async (url, options = {}) => {
    const { pathname } = new URL(url);
    if (options.method === 'PUT') {
      puts.push(JSON.parse(options.body));
      // Depop recounts after the write: the cost is what it wanted.
      draft = { ...draft, missing_fields: [], national_shipping_cost: '4.50' };
      return reply(200, draft);
    }
    if (pathname === '/presentation/api/v1/drafts/products/') return reply(200, { id: 'job-1' });
    if (pathname === '/api/v2/drafts/123456/') return reply(200, draft);
    return reply(200, draft);
  };
  const result = await c.depopApiAction('publish', {
    action: 'publish', confirm: true, draft_url: DRAFT, listing, deadline: Date.now() + 30000,
    depop_fields: { product_type: 'tshirts', national_shipping_cost: '4.50',
      shipping_methods: [{ payer: 'buyer', parcel_size: 'small', shipping_provider_id: 'USPS', ship_from_address_id: 2 }] },
  });
  assert.equal(result.publish_job_id, 'job-1');
  assert.equal(puts.length, 1);
  assert.equal(puts[0].national_shipping_cost, '4.50');
  // The repair keeps the draft's own pictures.
  assert.deepEqual(puts[0].pictures, [9]);
});

test('a draft missing a category is not repaired at publish; it names what the seller must fill', async () => {
  const c = loadWorker();
  c.chrome.cookies = { get: async () => ({ value: 'private-session' }) };
  c.marketplaceSleep = async () => {};
  let puts = 0;
  const draft = { id: '123456', description: 'Private draft', price_amount: '12', pictures: [{ id: 9 }],
    missing_fields: ['national_shipping', 'product_type'] };
  c.fetch = async (url, options = {}) => {
    if (options.method === 'PUT') puts += 1;
    return reply(200, draft);
  };
  const result = await c.depopApiAction('publish', {
    action: 'publish', confirm: true, draft_url: DRAFT, listing, deadline: Date.now() + 30000,
    depop_fields: { national_shipping_cost: '4.50' },
  });
  assert.equal(puts, 0);
  assert.match(result.error, /category/);
});

test('a locally chosen Depop size label resolves at publication and wins over an older size id', () => {
  const c = loadWorker();
  const attributes = { category_size_mapping: [{ department: 'menswear', product_type: 'jeans', size_set_by_region: { US: 10 } }],
    size_sets: [{ id: 10, sizes: [{ id: 1, name: '30' }, { id: 2, name: '32' }, { id: 3, name: 'Other' }] }] };
  const category = { department: 'menswear', productType: 'jeans' };
  const read = specifics => JSON.parse(JSON.stringify(call(c, 'depopSize', { depop_specifics: specifics }, category, attributes)));
  assert.deepEqual(read({ size: '32', nativeSizeId: 1 }), { set: 10, id: 2 });
  assert.deepEqual(read({ size: 'Other' }), { set: 10, id: 3 });
  assert.ok(read({ size: 'not offered', nativeSizeId: 1 }).problem);
  assert.ok(read({ size: '', nativeSizeId: 1 }).problem);
});
