const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

// Vinted embeds the token in an escaped JSON string on its own page.
const PAGE = '<html><script>{\\"CSRF_TOKEN\\":\\"csrf-secret\\"}</script></html>';
const USER = { user: { id: 7, login: 'seller', email: 'seller@example.com', currency: 'USD' } };
const CATALOGS = { catalogs: [{ id: 1, title: 'Women', catalogs: [{ id: 2, title: 'Clothing', catalogs: [
  { id: 3, title: 'Jeans', catalogs: [{ id: 1904, title: 'Straight jeans' }] }] }] }] };
const ATTRIBUTES = { attributes: [
  { code: 'condition', configuration: { options: [{ id: 3, title: 'Good' }, { id: 6, title: 'Very good' }] } },
  { code: 'size', configuration: { options: [] } },
] };
// Vinted's own women's clothing sizes, as its sell form answers them.
const WOMENS_SIZES = { attributes: [
  { code: 'condition', configuration: { options: [{ id: 3, title: 'Good' }] } },
  { code: 'size', configuration: { options: [
    { id: 1226, title: 'XXXS / US 00' }, { id: 102, title: 'XXS / US 0' }, { id: 2, title: 'XS / US 2' },
    { id: 3, title: 'S / US 4-6' }, { id: 4, title: 'M / US 8-10' }, { id: 5, title: 'L / US 12-14' }] } },
] };
// The same package sizes the shipping service sends this seller in French.
const FRENCH_PACKAGES = { package_sizes: [
  { id: 3, code: 'SMALL', title: 'Petit' }, { id: 2, code: 'MEDIUM', title: 'Moyen' }, { id: 4, code: 'LARGE', title: 'Grand' }] };
const DRAFT = {
  id: 42, title: 'Test jacket', description: 'Private draft', price: { amount: '24.0' },
  catalog_id: 1904, status_id: 3, is_draft: true, is_closed: false, is_hidden: false, is_reserved: false,
  assigned_photos: [{ id: 11 }], item_attributes: [{ code: 'condition', ids: [3] }],
};
const listing = {
  title: 'Test jacket', description: 'Private draft', price: 24,
  vinted_specifics: { category: 'Women > Clothing > Jeans > Straight jeans', condition: 'Good' },
};
const DRAFT_URL = 'https://www.vinted.com/items/42/edit';

// Chrome holds Vinted's hour-long access token and its week-long refresh token.
function token(expiresIn) {
  const claims = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + expiresIn }));
  return `e30.${claims.replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_')}.sig`;
}

function worker({ read, attributes = ATTRIBUTES, accessToken = token(3600), currentUser, packages } = {}) {
  const c = loadWorker();
  const journal = {};
  let current = 0;  // fresh hour, or one that ran out, or the refresh refused
  c.chrome.cookies = {
    get: async ({ name }) => (name === 'access_token_web' && accessToken
      ? { value: accessToken, expirationDate: Math.floor(Date.now() / 1000) + 3600 } : null),
  };
  c.chrome.storage = {
    local: { get: async key => ({ [key]: journal[key] }), set: async values => Object.assign(journal, values), remove: async () => {} },
  };
  c.chrome.tabs = { create: () => { throw new Error('opened a tab'); }, query: () => { throw new Error('read tabs'); } };
  c.nativePhoto = async () => ({ base64: 'AA==', mime_type: 'image/jpeg' });
  const requests = [];
  let reads = 0;
  c.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    const parsed = new URL(url);
    requests.push({ method, url, pathname: parsed.pathname, headers: options.headers, credentials: options.credentials, body: options.body });
    if (parsed.pathname === '/items/new') return reply(200, PAGE);
    if (parsed.pathname === '/web/api/auth/refresh') return reply(200, { access_token: token(3600) });
    if (parsed.pathname === '/api/v2/users/current') {
      current += 1;
      return currentUser ? currentUser(current) : reply(200, USER);
    }
    if (parsed.pathname === '/api/v2/item_upload/brands') return reply(200, { brands: [{ id: 1, title: 'Nike' }, { id: 2, title: 'Nike' }] });
    if (parsed.pathname === '/api/v2/item_upload/catalogs') return reply(200, CATALOGS);
    if (parsed.pathname === '/api/v2/item_upload/attributes') return reply(200, attributes);
    if (parsed.pathname === '/api/v2/item_upload/size_groups') return reply(200, { size_groups: [] });
    if (parsed.pathname.endsWith('/package_sizes')) return reply(200, packages || { package_sizes: [{ id: 2, title: 'Medium', code: 'MEDIUM' }, { id: 1, title: 'Small', code: 'SMALL' }] });
    if (parsed.pathname === '/api/v2/photos') return reply(200, { id: 11, orientation: 0 });
    if (method === 'POST' && parsed.pathname === '/api/v2/item_upload/drafts') return reply(200, { draft: { id: 42 } });
    if (method === 'POST' && parsed.pathname === '/api/v2/item_upload/drafts/42/completion') return reply(200, { item: { id: 42 } });
    if (parsed.pathname === '/api/v2/item_upload/items/42' || parsed.pathname === '/api/v2/items/42') {
      reads += 1;
      const source = read ? read(reads, parsed.pathname) : DRAFT;
      if (!source) return reply(404, { message: 'not found' });
      return reply(200, { item: source });
    }
    return reply(404, { message: 'missing' });
  };
  const sent = (method, path) => requests.filter(request => request.method === method && request.pathname === path);
  return { c, requests, journal, sent };
}

test('a Vinted hour that ran out is renewed before the draft, and keeps the sign-in', async () => {
  const { c, requests, sent } = worker({ accessToken: token(-60) });
  const result = await c.runVintedNative(message());
  assert.equal(result.ok, true);
  assert.equal(sent('POST', '/web/api/auth/refresh').length, 1);
  assert.equal(requests[0].pathname, '/web/api/auth/refresh');
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 1);
  assert.equal(JSON.stringify(result).includes(token(-60)), false);
});

test('a fresh Vinted hour is used without asking for a new one', async () => {
  const { c, sent } = worker();
  assert.equal((await c.runVintedNative(message())).ok, true);
  assert.equal(sent('POST', '/web/api/auth/refresh').length, 0);
});

test('a Vinted sign-in that has really ended says to reconnect and writes nothing', async () => {
  const { c, sent } = worker({ accessToken: null });
  c.fetch = ((inner) => async (url, options = {}) => (new URL(url).pathname === '/web/api/auth/refresh'
    ? reply(401, { message_code: 'invalid_authentication_token' }) : inner(url, options)))(c.fetch);
  const result = await c.runVintedNative(message());
  assert.equal(result.ok, false);
  assert.equal(result.error_code, 'signed_out');
  assert.match(result.error, /Reconnect Vinted/);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
  assert.equal(sent('POST', '/api/v2/photos').length, 0);
});

test('a token that turns over mid-call is renewed once and the call is retried', async () => {
  const { c, sent } = worker({ currentUser: n => (n < 2 ? reply(401, { message_code: 'invalid_authentication_token' }) : reply(200, USER)) });
  assert.equal((await c.runVintedNative(message())).ok, true);
  assert.equal(sent('POST', '/web/api/auth/refresh').length, 1);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 1);
});

const message = extra => ({ job_id: 'job-1', payload: {
  marketplace: 'vinted', action: 'save_draft', operation_id: 'op-1', confirm: false, listing,
  photos: [{ id: 'p1', mime_type: 'image/jpeg' }], ...extra,
} });

test('a new Vinted draft uploads a photo, saves once, and is confirmed by reading it back, with no tab', async () => {
  const { c, requests, journal, sent } = worker();
  const result = await c.runVintedNative(message());
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, state: 'draft', draft_url: DRAFT_URL });
  assert.equal(sent('POST', '/api/v2/photos').length, 1);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 1);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts/42/completion').length, 0);
  assert.equal(sent('GET', '/api/v2/item_upload/items/42').length, 1);
  const photo = sent('POST', '/api/v2/photos')[0];
  assert.equal(photo.body.get('photo[type]'), 'item');
  assert.equal(photo.body.get('photo[file]') instanceof c.Blob, true);
  const saved = JSON.parse(sent('POST', '/api/v2/item_upload/drafts')[0].body);
  assert.equal(saved.draft.title, 'Test jacket');
  assert.equal(saved.draft.catalog_id, 1904);
  assert.equal(saved.draft.status_id, 3);
  assert.deepEqual(saved.draft.assigned_photos, [{ id: 11, orientation: 0 }]);
  assert.equal(saved.push_up, undefined);
  assert.ok(requests.filter(request => request.pathname !== '/items/new').every(request => request.headers['x-csrf-token'] === 'csrf-secret' && request.credentials === 'include'));
  assert.equal(journal.native_operation_op_1, undefined);
  assert.equal(journal['native_operation_op-1'].receipt_url, DRAFT_URL);
  assert.equal(JSON.stringify(result).includes('csrf-secret'), false);
  assert.equal(JSON.stringify(result).includes('seller@example.com'), false);
});

test('publishing a Vinted draft completes it once and returns the public listing', async () => {
  const { c, sent } = worker({ read: n => (n < 2 ? DRAFT : { ...DRAFT, is_draft: false }) });
  const result = await c.runVintedNative(message({ action: 'publish', confirm: true, draft_url: DRAFT_URL }));
  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    ok: true, state: 'published', draft_url: DRAFT_URL, listing_url: 'https://www.vinted.com/items/42',
  });
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
  assert.equal(sent('POST', '/api/v2/photos').length, 0);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts/42/completion').length, 1);
  const body = JSON.parse(sent('POST', '/api/v2/item_upload/drafts/42/completion')[0].body);
  assert.equal(body.push_up, false);
  assert.equal(body.draft.assigned_photos, undefined);
  assert.deepEqual(body.draft.photos, [{ id: 11, orientation: 0 }]);
  assert.deepEqual(body.draft.item_attributes, [{ code: 'condition', ids: [3] }]);
});

test('a Vinted listing that is already live is not published again', async () => {
  const { c, sent } = worker({ read: () => ({ ...DRAFT, is_draft: false }) });
  const result = await c.runVintedNative(message({ action: 'publish', confirm: true, draft_url: DRAFT_URL }));
  assert.equal(result.state, 'published');
  assert.equal(result.listing_url, 'https://www.vinted.com/items/42');
  assert.equal(sent('POST', '/api/v2/item_upload/drafts/42/completion').length, 0);
});

test('checking a lost Vinted draft reads it and does not create another', async () => {
  const { c, sent } = worker();
  const result = await c.runVintedNative(message({ draft_url: DRAFT_URL }), true);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, state: 'draft', draft_url: DRAFT_URL });
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
  assert.equal(sent('POST', '/api/v2/photos').length, 0);
  assert.equal(sent('PUT', '/api/v2/item_upload/drafts/42').length, 0);
});

test('checking a lost Vinted publish reads the live listing and does not complete it again', async () => {
  const { c, sent } = worker({ read: () => ({ ...DRAFT, is_draft: false }) });
  const result = await c.runVintedNative(message({ action: 'publish', confirm: true, draft_url: DRAFT_URL }), true);
  assert.equal(result.state, 'published');
  assert.equal(result.listing_url, 'https://www.vinted.com/items/42');
  assert.equal(sent('POST', '/api/v2/item_upload/drafts/42/completion').length, 0);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
});

test('a Vinted draft that does not match is not published', async () => {
  const { c, sent } = worker({ read: () => ({ ...DRAFT, title: 'Other jacket' }) });
  const result = await c.runVintedNative(message({ action: 'publish', confirm: true, draft_url: DRAFT_URL }));
  assert.equal(result.ok, false);
  assert.match(result.error, /title/);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts/42/completion').length, 0);
});

test('a missing Vinted draft is reported and not created during publish', async () => {
  const { c, sent } = worker({ read: () => null });
  const result = await c.runVintedNative(message({ action: 'publish', confirm: true, draft_url: DRAFT_URL }));
  assert.equal(result.ok, false);
  assert.match(result.error, /Could not find draft/);
  assert.equal(result.safe_to_retry, true);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts/42/completion').length, 0);
});

test('Vinted refuses to publish without the seller confirming, and does not call Vinted', async () => {
  const { c, requests } = worker();
  const result = await c.runVintedNative(message({ action: 'publish', confirm: false, draft_url: DRAFT_URL }));
  assert.match(result.error, /Confirm/);
  assert.equal(result.safe_to_retry, true);
  assert.equal(requests.length, 0);
});

test('a Vinted listing without a category does not save a draft', async () => {
  const { c, sent } = worker();
  const bare = { title: 'Test jacket', description: 'Private draft', price: 24, vinted_specifics: { condition: 'Good' } };
  const result = await c.runVintedNative(message({ listing: bare }));
  assert.equal(result.ok, false);
  assert.equal(result.error_code, 'missing_fields');
  assert.match(result.error, /category/);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
  assert.equal(sent('GET', '/api/v2/item_upload/catalogs').length, 0);
});

test('the app size is matched to Vinted\'s own size title', async () => {
  const cases = [
    ['6', 3],          // General US numeric falls in "S / US 4-6"
    ['S / 6', 3],      // the bottoms mapping's form
    ['S', 3],
    ['M', 4],
    ['M / 10', 4],
    ['US 00', 1226],
    ['xxxS / US 00', 1226],
  ];
  for (const [size, id] of cases) {
    const { c, sent } = worker({ attributes: WOMENS_SIZES, read: () => ({ ...DRAFT, size_id: id }) });
    const result = await c.runVintedNative(message({ listing: { ...listing, vinted_specifics: { ...listing.vinted_specifics, size } } }));
    assert.equal(result.ok, true, size);
    const saved = JSON.parse(sent('POST', '/api/v2/item_upload/drafts')[0].body).draft;
    assert.equal(saved.size_id, id, size);
    assert.deepEqual(saved.item_attributes, [{ code: 'condition', ids: [3] }, { code: 'size', ids: [id] }], size);
  }
});

test('a Vinted draft reads size from its native attributes', async () => {
  const { c } = worker({ attributes: WOMENS_SIZES, read: () => ({ ...DRAFT,
    item_attributes: [{ code: 'condition', ids: [3] }, { code: 'size', ids: [3] }],
  }) });
  const result = await c.runVintedNative(message({ listing: { ...listing, vinted_specifics: { ...listing.vinted_specifics, size: '6' } } }));
  assert.equal(result.ok, true);
});

test('checking a saved draft still refuses a size mismatch and writes nothing', async () => {
  const { c, requests } = worker({ attributes: WOMENS_SIZES, read: () => ({ ...DRAFT, size_id: 4 }) });
  const result = await c.runVintedNative(message({ draft_url: DRAFT_URL, expected_photo_count: 1,
    listing: { ...listing, vinted_specifics: { ...listing.vinted_specifics, size: '6' } },
  }), true);
  assert.equal(result.ok, false);
  assert.match(result.error, /size/);
  assert.equal(requests.filter(request => request.method !== 'GET'
    && request.pathname !== '/api/v2/item_upload/attributes').length, 0);
});

test('a Vinted size left blank on its form uses the listing\'s General size', async () => {
  const { c, sent } = worker({ attributes: WOMENS_SIZES, read: () => ({ ...DRAFT, size_id: 3 }) });
  const { size, ...specifics } = listing.vinted_specifics;
  const result = await c.runVintedNative(message({ listing: { ...listing, size: '6', vinted_specifics: specifics } }));
  assert.equal(result.ok, true);
  const saved = JSON.parse(sent('POST', '/api/v2/item_upload/drafts')[0].body).draft;
  assert.equal(saved.size_id, 3);
});

test('a size Vinted does not offer is refused instead of guessed', async () => {
  const { c, sent } = worker({ attributes: WOMENS_SIZES });
  for (const size of ['Elle', '0.5', '42']) {
    const result = await c.runVintedNative(message({ listing: { ...listing, vinted_specifics: { ...listing.vinted_specifics, size } } }));
    assert.equal(result.ok, false, size);
    assert.equal(result.error_code, 'missing_fields', size);
  }
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
});

test('the package size is matched by its stable code when Vinted answers in another language', async () => {
  const { c, sent } = worker({ attributes: WOMENS_SIZES, packages: FRENCH_PACKAGES, read: () => ({ ...DRAFT, size_id: 3, package_size_id: 2 }) });
  const result = await c.runVintedNative(message({ listing: { ...listing, vinted_specifics: { ...listing.vinted_specifics, size: '6' } } }));
  assert.equal(result.ok, true);
  const saved = JSON.parse(sent('POST', '/api/v2/item_upload/drafts')[0].body).draft;
  assert.equal(saved.package_size_id, 2);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 1);
});

// Every Vinted call asks for English names, so the app's own choices match.
test('Vinted requests carry the Locale its sell page sends', async () => {
  const { c, requests } = worker();
  await c.runVintedNative(message());
  const calls = requests.filter(request => request.pathname !== '/items/new');
  assert.ok(calls.length > 0);
  for (const request of calls) assert.equal(request.headers?.Locale, 'en-US', request.pathname);
});

test('Good does not match Very good, and that refusal writes nothing', async () => {
  const { c, sent } = worker({ attributes: { attributes: [
    { code: 'condition', configuration: { options: [{ id: 6, title: 'Very good' }] } },
  ] } });
  const result = await c.runVintedNative(message());
  assert.equal(result.ok, false);
  assert.match(result.error, /condition/);
  assert.equal(sent('POST', '/api/v2/item_upload/drafts').length, 0);
  assert.equal(sent('POST', '/api/v2/photos').length, 0);
});


test('brand search uses the category and keyword and makes only read requests', async () => {
  const { c, requests } = worker();
  const options = await c.vintedBrandOptions({ query: 'Ni &', category: listing.vinted_specifics.category });
  assert.deepEqual(Array.from(options), ['Nike']);
  const search = new URL(requests.find(row => row.pathname.endsWith('/brands')).url);
  assert.equal(search.searchParams.get('keyword'), 'Ni &');
  assert.equal(search.searchParams.get('category_id'), '1904');
  assert.ok(requests.every(row => row.method === 'GET'));
});
