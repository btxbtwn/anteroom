const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

const USER = 'a'.repeat(24);
const POST = 'b'.repeat(24);
const DISCOUNT = 'c'.repeat(24);
const jwt = `x.${Buffer.from(JSON.stringify({ user_id: USER })).toString('base64')}.y`;
const PAGE = `<html><script>window.__INITIAL_STATE__={"csrftoken":"page-csrf","userCookies":{"userInfo":{"uid":"${USER}"}}};</script></html>`;
const RULES = { offer_to_likers__us: {
  A: { enabled: true, minimum_seller_offer_discount_percent: 10, maximum_seller_offer_discount_percent: 79 },
  B: { enabled: true, minimum_seller_offer_discount_percent: 12, maximum_seller_offer_discount_percent: 70 },
  C: { enabled: false, minimum_seller_offer_discount_percent: 50, maximum_seller_offer_discount_percent: 60 },
} };
const posh = `poshmark.com/vm-rest/posts/${POST}`;
const share = `poshmark.com/vm-rest/users/self/shared_posts/${POST}`;
const offer = `poshmark.com/vm-rest/posts/${POST}/likes/users/offers`;
const livePost = { data: { creator_id: USER, status: 'published', price_amount: { val: '50', currency_code: 'USD' }, like_count: 3,
  inventory: { status: 'available', size_quantities: [{ quantity_sold: 0 }] } } };

function worker(routes) {
  const c = loadWorker();
  const store = {};
  c.chrome.cookies = { get: async ({ name }) => ({ jwt: { value: jwt } })[name] };
  c.chrome.storage = { local: {
    get: async key => (key in store ? { [key]: structuredClone(store[key]) } : {}),
    set: async values => { for (const [key, value] of Object.entries(values)) store[key] = structuredClone(value); },
  } };
  const requests = [];
  const bodies = {};
  routes = { 'GET poshmark.com/create-listing': [200, PAGE, { 'content-type': 'text/html' }],
    'GET poshmark.com/vm-rest/meta/feature_settings': [200, RULES], [`GET ${posh}`]: [200, livePost], ...routes };
  c.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    const key = `${options.method || 'GET'} ${parsed.hostname}${parsed.pathname}`;
    requests.push(key);
    if (options.body) bodies[key] = JSON.parse(options.body);
    const answer = routes[key];
    const [status, body, headers] = typeof answer === 'function' ? answer(parsed) : answer || [404, {}];
    return reply(status, body, headers);
  };
  const results = [];
  c.sendBrowserResult = (_msg, result) => results.push(JSON.parse(JSON.stringify(result)));
  return { c, requests, bodies, store, results };
}

const promote = (c, payload) => c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: {
  marketplace: 'poshmark', listing_id: POST, confirm: true, action_id: 'action-1', ...payload } });

test('share sends Poshmark’s own share once and journals the result', async () => {
  const { c, requests, results, store } = worker({ [`PUT ${share}`]: [200, { data: {} }] });
  await promote(c, { action: 'share' });
  assert.deepEqual(results[0], { ok: true, state: 'done' });
  assert.equal(requests.filter(r => r === `PUT ${share}`).length, 1);
  assert.equal(store['promotion_action-1'].finished, true);
  // The same action again answers from the journal and sends nothing.
  await promote(c, { action: 'share' });
  assert.deepEqual(results[1], { ok: true, state: 'done' });
  assert.equal(requests.filter(r => r === `PUT ${share}`).length, 1);
});

test('an interrupted action is reported as uncertain and never repeated', async () => {
  const { c, requests, results, store } = worker({ [`PUT ${share}`]: [200, { data: {} }] });
  store['promotion_action-1'] = { finished: false };
  await promote(c, { action: 'share' });
  assert.equal(results[0].ok, false);
  assert.equal(results[0].error_code, 'uncertain');
  assert.ok(!requests.some(r => r.startsWith('PUT')));
  await c.handleMarketplacePromotion({ type: 'marketplace.promotion_result', payload: { action_id: 'action-1' } });
  assert.deepEqual(results[1], { finished: false, known: true });
});

test('a share limit stops with a rate-limit refusal; a lost reply is uncertain', async () => {
  const limited = worker({ [`PUT ${share}`]: [200, { error: { errorType: 'SharePostRequestLimitExceededError', userMessage: 'Slow down' } }] });
  await promote(limited.c, { action: 'share' });
  assert.equal(limited.results[0].error_code, 'rate_limited');
  assert.equal(limited.results[0].safe_to_retry, true);
  const captcha = worker({ [`PUT ${share}`]: [200, { error: { errorType: 'DisplayCaptchaError' } }] });
  await promote(captcha.c, { action: 'share' });
  assert.equal(captcha.results[0].error_code, 'challenge');
  const lost = worker({ [`PUT ${share}`]: [502, 'bad gateway'] });
  await promote(lost.c, { action: 'share' });
  assert.equal(lost.results[0].error_code, 'uncertain');
  assert.equal(lost.results[0].safe_to_retry, false);
});

test('never shares or offers on a sold or foreign listing', async () => {
  const sold = { data: { ...livePost.data, inventory: { status: 'sold_out', size_quantities: [{ quantity_sold: 1 }] } } };
  const foreign = { data: { ...livePost.data, creator_id: 'd'.repeat(24) } };
  for (const body of [sold, foreign]) {
    for (const action of ['share', 'offer']) {
      const { c, requests, results } = worker({ [`GET ${posh}`]: [200, body] });
      await promote(c, { action, price_cents: 5000, offer_price_cents: 4000, floor_cents: 3000 });
      assert.equal(results[0].ok, false);
      assert.ok(!requests.some(r => r.startsWith('PUT') || r.startsWith('POST')));
    }
  }
});

test('check reads price, likers, the strictest offer rule and shipping discounts without writing', async () => {
  const { c, requests, results } = worker({
    [`GET poshmark.com/vm-rest/users/${USER}/seller_shipping_discounts/offer`]: url => {
      assert.equal(JSON.parse(url.searchParams.get('price_amount')).val, '42');
      assert.equal(url.searchParams.get('object_id'), POST);
      return [200, { data: [{ id: DISCOUNT, label: '-$1.50', min_offer_amount: 5 }] }];
    },
  });
  await c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'poshmark', action: 'offer',
    listing_id: POST, discount_percent: 15 } });
  assert.deepEqual(results[0], { ok: true, price_cents: 5000, like_count: 3, minimum_discount_percent: 12, maximum_discount_percent: 70,
    offer_price_cents: 4200, shipping_discounts: [{ id: DISCOUNT, label: '-$1.50', min_offer_cents: 500 }] });
  assert.ok(requests.every(r => r.startsWith('GET')));
  await c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'poshmark', action: 'offer',
    listing_id: POST, discount_percent: 10 } });
  assert.equal(results[1].offer_price_cents, null);
});

test('offer to likers sends Poshmark’s own body and refuses below the floor or a changed price', async () => {
  const { c, bodies, results } = worker({ [`POST ${offer}`]: [200, { data: {} }] });
  await promote(c, { action: 'offer', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3500, shipping_discount_id: DISCOUNT });
  assert.equal(results[0].ok, true);
  assert.deepEqual(bodies[`POST ${offer}`], { offer_amount: { val: '40', currency_code: 'USD' }, offer_api_version: 3,
    seller_shipping_discount: { id: DISCOUNT } });
  const cases = [
    { offer_price_cents: 3000, floor_cents: 3500 },
    { offer_price_cents: 4000, floor_cents: 0 },
    { offer_price_cents: 4500, floor_cents: 3500 },
    { price_cents: 6000, offer_price_cents: 4000, floor_cents: 3500 },
  ];
  for (const [index, overrides] of cases.entries()) {
    const run = worker({ [`POST ${offer}`]: [200, { data: {} }] });
    await promote(run.c, { action: 'offer', price_cents: 5000, ...overrides, action_id: `action-${index + 2}` });
    assert.equal(run.results[0].ok, false);
    assert.ok(!run.requests.some(r => r.startsWith('POST')), JSON.stringify(overrides));
  }
});

test('no likers means no offer, and an unconfirmed action is refused', async () => {
  const none = worker({ [`GET ${posh}`]: [200, { data: { ...livePost.data, like_count: 0 } }] });
  await promote(none.c, { action: 'offer', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3000 });
  assert.equal(none.results[0].ok, false);
  assert.ok(!none.requests.some(r => r.startsWith('POST')));
  const unconfirmed = worker({});
  await promote(unconfirmed.c, { action: 'share', confirm: false });
  assert.equal(unconfirmed.results[0].ok, false);
  assert.deepEqual(unconfirmed.requests, []);
});

test('a scheduled round can slow shares down but never below Poshmark’s spacing', async () => {
  for (const [gap, expected] of [[15000, 15000], [1000, 4000], [999999, 30000]]) {
    const { c, store } = worker({ [`PUT ${share}`]: [200, { data: {} }] });
    const sleeps = [];
    c.marketplaceSleep = async ms => { sleeps.push(ms); };
    store.poshmark_last_share_at = Date.now();
    await promote(c, { action: 'share', gap_ms: gap });
    assert.equal(sleeps.length, 1);
    assert.ok(sleeps[0] > expected - 1000 && sleeps[0] <= expected + 3000, `${gap} → ${sleeps[0]}`);
  }
});

const vm = require('node:vm');
const ITEM = 'm12345678901';
const SLUG = 'seller-vintage-tee';

function offerWorker(marketplace, answers) {
  const c = loadWorker();
  const store = {};
  c.chrome.cookies = { get: async ({ name }) => (name === 'access_token' ? { value: 'depop-token' } : null) };
  c.chrome.storage = { local: {
    get: async key => (key in store ? { [key]: structuredClone(store[key]) } : {}),
    set: async values => { for (const [key, value] of Object.entries(values)) store[key] = structuredClone(value); },
  } };
  const requests = [];
  c.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    const body = options.body ? JSON.parse(options.body) : null;
    const key = `${options.method || 'GET'} ${parsed.hostname}${parsed.pathname}${body?.operationName ? ` ${body.operationName}` : ''}`;
    requests.push({ key, body, headers: options.headers });
    const answer = answers[key];
    const [status, data] = typeof answer === 'function' ? answer(body) : answer || [404, {}];
    return reply(status, data);
  };
  const results = [];
  c.sendBrowserResult = (_msg, result) => results.push(JSON.parse(JSON.stringify(result)));
  return { c, requests, results, store };
}

const MERCARI_FAKE = `PENDING_OFFER_REQUESTS.mercari = {
  likers: { operationName: 'fakeLikersQuery', sha256Hash: 'l'.repeat(64), variables: id => ({ id }),
    read: data => ({ like_count: data.likers.count, lowest_price_cents: data.likers.low }) },
  offer: { operationName: 'fakeOfferMutation', sha256Hash: 'o'.repeat(64), variables: (id, cents) => ({ id, cents }),
    confirmed: data => data.offer?.ok === true },
}`;
const mercariAnswers = (overrides = {}) => ({
  'GET www.mercari.com/v1/initialize': [200, { csrf: 'csrf', accessToken: 'token', user: { userId: 77, name: 'Seller' } }],
  'POST www.mercari.com/v1/api sellFetchItemsDetails': [200, { data: { item: { itemId: ITEM, status: 'on_sale', price: 5000, seller: { id: 77 } } } }],
  'POST www.mercari.com/v1/api fakeLikersQuery': [200, { data: { likers: { count: 4, low: 4500 } } }],
  'POST www.mercari.com/v1/api fakeOfferMutation': [200, { data: { offer: { ok: true } } }],
  ...overrides,
});

test('Mercari and Depop offers refuse before any request until their requests are captured', async () => {
  for (const [marketplace, id] of [['mercari', ITEM], ['depop', SLUG]]) {
    const { c, requests, results } = offerWorker(marketplace, {});
    await c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace, action: 'offer', listing_id: id, discount_percent: 20 } });
    await c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace, action: 'offer', listing_id: id, confirm: true,
      action_id: 'action-1', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3000 } });
    await c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace, action: 'share', listing_id: id, confirm: true,
      action_id: 'action-2' } });
    assert.deepEqual(results.map(r => [r.ok, r.error_code, r.safe_to_retry]), [[false, 'not_captured', true], [false, 'not_captured', true], [false, 'rejected', true]]);
    assert.deepEqual(requests, []);
  }
});

test('Mercari offer to likers follows its lowest-price rule and sends the captured operation once', async () => {
  const check = offerWorker('mercari', mercariAnswers());
  vm.runInContext(MERCARI_FAKE, check.c);
  for (const percent of [15, 20]) {
    await check.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'mercari', action: 'offer',
      listing_id: ITEM, discount_percent: percent } });
  }
  // 10% below the $45 low is $40.50, so whole dollars allow $40: at least 20% off $50.
  assert.deepEqual(check.results[0], { ok: true, price_cents: 5000, like_count: 4, minimum_discount_percent: 20, maximum_discount_percent: 99,
    shipping_discounts: [], offer_price_cents: null });
  assert.equal(check.results[1].offer_price_cents, 4000);
  assert.ok(!check.requests.some(r => r.key.endsWith('fakeOfferMutation')));

  const sent = offerWorker('mercari', mercariAnswers());
  vm.runInContext(MERCARI_FAKE, sent.c);
  const payload = { marketplace: 'mercari', action: 'offer', listing_id: ITEM, confirm: true, action_id: 'action-1',
    price_cents: 5000, offer_price_cents: 4000, floor_cents: 3500 };
  await sent.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload });
  await sent.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload });
  assert.deepEqual(sent.results[0], { ok: true, state: 'done', recipient_count: 4 });
  assert.deepEqual(sent.results[1], sent.results[0]);
  const offers = sent.requests.filter(r => r.key.endsWith('fakeOfferMutation'));
  assert.equal(offers.length, 1);
  assert.deepEqual(offers[0].body.variables, { id: ITEM, cents: 4000 });
  assert.equal(offers[0].body.extensions.persistedQuery.sha256Hash, 'o'.repeat(64));
});

test('Mercari never offers below the floor, above its rule, on a changed price or someone else’s item', async () => {
  const cases = [
    [{}, { offer_price_cents: 4000, floor_cents: 4500 }],
    [{}, { offer_price_cents: 4100, floor_cents: 3000 }],
    [{}, { offer_price_cents: 4500, floor_cents: 3000 }],
    [{}, { price_cents: 6000, offer_price_cents: 4000, floor_cents: 3000 }],
    [{ 'POST www.mercari.com/v1/api sellFetchItemsDetails': [200, { data: { item: { itemId: ITEM, status: 'on_sale', price: 5000, seller: { id: 8 } } } }] }, {}],
    [{ 'POST www.mercari.com/v1/api sellFetchItemsDetails': [200, { data: { item: { itemId: ITEM, status: 'sold_out', price: 5000, seller: { id: 77 } } } }] }, {}],
    [{ 'POST www.mercari.com/v1/api fakeLikersQuery': [200, { data: { likers: { count: 0, low: 5000 } } }] }, {}],
  ];
  for (const [answers, overrides] of cases) {
    const run = offerWorker('mercari', mercariAnswers(answers));
    vm.runInContext(MERCARI_FAKE, run.c);
    await run.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'mercari', action: 'offer', listing_id: ITEM,
      confirm: true, action_id: 'action-1', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3000, ...overrides } });
    assert.equal(run.results[0].ok, false, JSON.stringify(overrides));
    assert.ok(!run.requests.some(r => r.key.endsWith('fakeOfferMutation')), JSON.stringify(overrides));
  }
  const lost = offerWorker('mercari', mercariAnswers({ 'POST www.mercari.com/v1/api fakeOfferMutation': [502, 'bad gateway'] }));
  vm.runInContext(MERCARI_FAKE, lost.c);
  await lost.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'mercari', action: 'offer', listing_id: ITEM,
    confirm: true, action_id: 'action-1', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3000 } });
  assert.deepEqual([lost.results[0].error_code, lost.results[0].safe_to_retry], ['uncertain', false]);
});

const DEPOP_FAKE = `PENDING_OFFER_REQUESTS.depop = {
  likers: { path: product => '/api/v1/fake/products/' + product.id + '/likers/', read: body => ({ like_count: body.count }) },
  offer: { method: 'POST', path: product => '/api/v1/fake/products/' + product.id + '/offers/', body: (product, cents) => ({ cents }),
    confirmed: body => body.ok === true },
}`;
const depopProduct = { slug: SLUG, id: 999, user_id: 55, status: 'STATUS_ONSALE', price_amount: '50.00', price_currency: 'USD' };
const depopAnswers = (overrides = {}) => ({
  'GET webapi.depop.com/presentation/api/v1/users/me/': [200, { id: 55 }],
  [`GET webapi.depop.com/presentation/api/v1/products/by-slug/${SLUG}/`]: [200, depopProduct],
  'GET webapi.depop.com/api/v1/fake/products/999/likers/': [200, { count: 2 }],
  'POST webapi.depop.com/api/v1/fake/products/999/offers/': [200, { ok: true }],
  ...overrides,
});

test('Depop offer to likers reads the product, refuses several sizes and sends the captured request once', async () => {
  const check = offerWorker('depop', depopAnswers());
  vm.runInContext(DEPOP_FAKE, check.c);
  await check.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer',
    listing_id: SLUG, discount_percent: 10 } });
  assert.deepEqual(check.results[0], { ok: true, price_cents: 5000, like_count: 2, minimum_discount_percent: 5, maximum_discount_percent: 99,
    shipping_discounts: [], offer_price_cents: 4500 });
  assert.ok(check.requests.every(r => r.key.startsWith('GET') && r.headers.Authorization === 'Bearer depop-token'));

  const sent = offerWorker('depop', depopAnswers());
  vm.runInContext(DEPOP_FAKE, sent.c);
  await sent.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'depop', action: 'offer', listing_id: SLUG,
    confirm: true, action_id: 'action-1', price_cents: 5000, offer_price_cents: 4500, floor_cents: 4000 } });
  assert.deepEqual(sent.results[0], { ok: true, state: 'done', recipient_count: 2 });
  const offers = sent.requests.filter(r => r.key.startsWith('POST'));
  assert.deepEqual(offers.map(r => r.body), [{ cents: 4500 }]);

  const sized = offerWorker('depop', depopAnswers({ [`GET webapi.depop.com/presentation/api/v1/products/by-slug/${SLUG}/`]:
    [200, { ...depopProduct, variants: { 1: { quantity: 1 }, 2: { quantity: 1 } } }] }));
  vm.runInContext(DEPOP_FAKE, sized.c);
  await sized.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'depop', action: 'offer', listing_id: SLUG,
    confirm: true, action_id: 'action-1', price_cents: 5000, offer_price_cents: 4500, floor_cents: 4000 } });
  assert.equal(sized.results[0].ok, false);
  assert.ok(!sized.requests.some(r => r.key.startsWith('POST')));
  const limited = offerWorker('depop', depopAnswers({ 'POST webapi.depop.com/api/v1/fake/products/999/offers/': [429, { message: 'Slow down' }] }));
  vm.runInContext(DEPOP_FAKE, limited.c);
  await limited.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'depop', action: 'offer', listing_id: SLUG,
    confirm: true, action_id: 'action-1', price_cents: 5000, offer_price_cents: 4500, floor_cents: 4000 } });
  assert.deepEqual([limited.results[0].error_code, limited.results[0].safe_to_retry], ['rate_limited', true]);
});
