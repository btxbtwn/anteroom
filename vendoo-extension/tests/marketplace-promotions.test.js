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

test('an offer without an extra discount keeps the listing’s own shipping discount', async () => {
  const LISTED = 'd'.repeat(24);
  const discounted = worker({ [`GET ${posh}`]: [200, { data: { ...livePost.data, seller_shipping_discount_id: LISTED } }],
    [`POST ${offer}`]: [200, { data: {} }] });
  await promote(discounted.c, { action: 'offer', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3500 });
  assert.equal(discounted.results[0].ok, true);
  assert.deepEqual(discounted.bodies[`POST ${offer}`].seller_shipping_discount, { id: LISTED });
  const plain = worker({ [`POST ${offer}`]: [200, { data: {} }] });
  await promote(plain.c, { action: 'offer', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3500 });
  assert.equal(plain.results[0].ok, true);
  assert.equal(plain.bodies[`POST ${offer}`].seller_shipping_discount, undefined);
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

// Replies in the shapes Mercari's offer screen recorded (2026-10-27), with made-up values.
const OFFER_SEND = 'POST www.mercari.com/v1/api createOfferToLikers';
const mercariItem = (item = {}) => [200, { data: { item: { itemId: ITEM, status: 'on_sale', price: 5000, seller: { id: 77 }, ...item } } }];
const mercariLikerable = (eligible = true, item = {}) => [200, { data: { offer: { offerToLikerable: eligible, __typename: 'Offer' },
  item: { id: ITEM, price: 5000, numLikes: 4, name: 'Jacket', photos: [], shippingPayer: { id: 1, name: 'Buyer', code: 'buyer' },
    shippingClasses: [{ id: 1, fee: 799, rateType: 'FIXED', standardFee: 799, nonPromotionalFee: 799 }], ...item } } }];
const mercariBounds = (lowerLimitCents = 2550, higherLimitCents = 4050) => [200, { data: {
  offerToLikerBounds: { lowerLimitCents, higherLimitCents, suggestedPriceCents: 4000, __typename: 'OfferToLikerBounds' },
  item: { shippingClasses: [], shippingPayer: { id: 1 } } } }];
const mercariAnswers = (overrides = {}) => ({
  'GET www.mercari.com/v1/initialize': [200, { csrf: 'csrf', accessToken: 'token', user: { userId: 77, name: 'Seller' } }],
  'POST www.mercari.com/v1/api sellFetchItemsDetails': mercariItem(),
  'POST www.mercari.com/v1/api offerToLikerable': mercariLikerable(),
  'POST www.mercari.com/v1/api offerToLikerInit': mercariBounds(),
  [OFFER_SEND]: [200, { data: { createOfferToLikers: { numOffersCreated: 3, __typename: 'CreateOfferToLikersResponse' } } }],
  ...overrides,
});
const mercariOffer = (c, payload = {}) => c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'mercari',
  action: 'offer', listing_id: ITEM, confirm: true, action_id: 'action-1', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3000, ...payload } });

test('an uncaptured marketplace refuses before any request; Mercari never shares', async () => {
  const depop = offerWorker('depop', {});
  vm.runInContext('PENDING_OFFER_REQUESTS.depop = { likers: null, offer: null }', depop.c);
  await depop.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer', listing_id: SLUG, discount_percent: 20 } });
  await depop.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'depop', action: 'offer', listing_id: SLUG, confirm: true,
    action_id: 'action-1', price_cents: 5000, offer_price_cents: 4000, floor_cents: 3000 } });
  assert.deepEqual(depop.results.map(r => [r.ok, r.error_code, r.safe_to_retry]), [[false, 'not_captured', true], [false, 'not_captured', true]]);
  assert.deepEqual(depop.requests, []);
  const mercari = offerWorker('mercari', mercariAnswers());
  await mercari.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'mercari', action: 'share', listing_id: ITEM,
    confirm: true, action_id: 'action-2' } });
  assert.deepEqual([mercari.results[0].ok, mercari.results[0].error_code], [false, 'rejected']);
  assert.deepEqual(mercari.requests, []);
});

test('Mercari check reads eligibility and Mercari’s own offer range, in whole dollars, without sending', async () => {
  const check = offerWorker('mercari', mercariAnswers());
  for (const percent of [15, 20, 50]) {
    await check.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'mercari', action: 'offer',
      listing_id: ITEM, discount_percent: percent } });
  }
  // $25.50 to $40.50 in cents becomes $26 to $40: 19% to 48% off $50 after rounding.
  assert.deepEqual(check.results[0], { ok: true, price_cents: 5000, like_count: 4, minimum_discount_percent: 19, maximum_discount_percent: 48,
    shipping_discounts: [], offer_price_cents: null });
  assert.equal(check.results[1].offer_price_cents, 4000);
  assert.equal(check.results[2].offer_price_cents, null);
  assert.ok(!check.requests.some(r => r.key === OFFER_SEND));
  const reads = check.requests.filter(r => /offerToLiker(able|Init)$/.test(r.key));
  assert.deepEqual(reads[0].body, { operationName: 'offerToLikerable', variables: { itemId: ITEM },
    extensions: { persistedQuery: { version: 1, sha256Hash: '37bcc049bd1bf9241387cf44f4a2742a653f2e3dca8150c26d00aa27e30cc825' } } });
  assert.equal(reads[1].body.extensions.persistedQuery.sha256Hash, 'babbde0d58f82b3803f24247c0e9f007688c75e3c3b9cbef96a7b33f055cc64a');
  assert.deepEqual(reads[1].body.variables, { itemId: ITEM });
  assert.ok(reads.every(r => r.headers['x-csrf-token'] === 'csrf' && r.headers.authorization === 'Bearer token'
    && !('x-socure-device-token' in r.headers)));
});

test('Mercari offer preflight declares the required item ID as non-null', async () => {
  const check = offerWorker('mercari', mercariAnswers());
  const fetch = check.c.fetch;
  check.c.fetch = async (url, options) => {
    const body = JSON.parse(options?.body || '{}');
    if (body.operationName === 'sellFetchItemsDetails' && !/\$itemId:\s*String!/.test(body.query)) {
      return reply(400, { errors: [{ message: 'Variable "$itemId" of type "String" used in position expecting type "String!".' }] });
    }
    return fetch(url, options);
  };
  await check.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: {
    marketplace: 'mercari', action: 'offer', listing_id: ITEM, discount_percent: 20,
  } });
  assert.equal(check.results[0].ok, true);
  assert.ok(!check.requests.some(r => r.key === OFFER_SEND));
});

test('Mercari accepts a 10% rule when whole-dollar rounding meets its actual bounds', async () => {
  const run = offerWorker('mercari', mercariAnswers({
    'POST www.mercari.com/v1/api sellFetchItemsDetails': mercariItem({ price: 1700 }),
    'POST www.mercari.com/v1/api offerToLikerable': mercariLikerable(true, { price: 1700 }),
    'POST www.mercari.com/v1/api offerToLikerInit': mercariBounds(100, 1500),
  }));
  for (const percent of [5, 10]) {
    await run.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: {
      marketplace: 'mercari', action: 'offer', listing_id: ITEM, discount_percent: percent,
    } });
  }
  assert.equal(run.results[0].offer_price_cents, null);
  assert.equal(run.results[1].offer_price_cents, 1500);
  assert.equal(run.results[1].minimum_discount_percent, 6);
  assert.ok(!run.requests.some(r => r.key === OFFER_SEND));
});

test('Mercari sends createOfferToLikers once, in cents, and answers a repeat from the journal', async () => {
  const sent = offerWorker('mercari', mercariAnswers());
  await mercariOffer(sent.c, { floor_cents: 3500 });
  await mercariOffer(sent.c, { floor_cents: 3500 });
  assert.deepEqual(sent.results[0], { ok: true, state: 'done', recipient_count: 3 });
  assert.deepEqual(sent.results[1], sent.results[0]);
  const offers = sent.requests.filter(r => r.key === OFFER_SEND);
  assert.equal(offers.length, 1);
  assert.deepEqual(offers[0].body, { operationName: 'createOfferToLikers', variables: { input: { itemId: ITEM, offerPrice: 4000 } },
    extensions: { persistedQuery: { version: 1, sha256Hash: '00c320abe9a24e864ad765b06b0276517b423e12f66203a27ffd9edd95ff36e6' } } });
});

test('Mercari never offers outside the floor or its range, on a changed price, or on an item it refuses', async () => {
  const cases = [
    [{}, { offer_price_cents: 4000, floor_cents: 4500 }],
    [{}, { offer_price_cents: 3950, floor_cents: 3000 }],
    [{}, { offer_price_cents: 4500, floor_cents: 3000 }],
    [{}, { offer_price_cents: 2500, floor_cents: 1000 }],
    [{}, { price_cents: 6000 }],
    [{ 'POST www.mercari.com/v1/api sellFetchItemsDetails': mercariItem({ seller: { id: 8 } }) }, {}],
    [{ 'POST www.mercari.com/v1/api sellFetchItemsDetails': mercariItem({ status: 'sold_out' }) }, {}],
    [{ 'POST www.mercari.com/v1/api offerToLikerable': mercariLikerable(true, { numLikes: 0 }) }, {}],
    [{ 'POST www.mercari.com/v1/api offerToLikerable': mercariLikerable(false) }, {}],
    [{ 'POST www.mercari.com/v1/api offerToLikerable': mercariLikerable(true, { price: 50 }) }, {}],
    // Bounds in dollars, or at the price, mean the units or the rule changed.
    [{ 'POST www.mercari.com/v1/api offerToLikerInit': mercariBounds(25, 40) }, {}],
    [{ 'POST www.mercari.com/v1/api offerToLikerInit': mercariBounds(2550, 5000) }, {}],
    [{ 'POST www.mercari.com/v1/api offerToLikerInit': [200, { data: { offerToLikerBounds: null } }] }, {}],
  ];
  for (const [answers, overrides] of cases) {
    const run = offerWorker('mercari', mercariAnswers(answers));
    await mercariOffer(run.c, overrides);
    const label = JSON.stringify([Object.keys(answers), overrides]);
    assert.deepEqual([run.results[0].ok, run.results[0].safe_to_retry], [false, true], label);
    assert.ok(!run.requests.some(r => r.key === OFFER_SEND), label);
  }
});

test('Mercari offer outcomes: a lost or unreadable reply is uncertain; a refusal is sent only once', async () => {
  const outcomes = [
    [[502, 'bad gateway'], 'uncertain', false],
    [[200, { data: { createOfferToLikers: null } }], 'uncertain', false],
    [[200, { data: { createOfferToLikers: { numOffersCreated: 0 } } }], 'rejected', true],
    [[401, { message: 'Unauthorized' }], 'signed_out', true],
    [[429, { message: 'Too many requests' }], 'rate_limited', true],
  ];
  for (const [answer, code, safe] of outcomes) {
    const run = offerWorker('mercari', mercariAnswers({ [OFFER_SEND]: answer }));
    await mercariOffer(run.c);
    assert.deepEqual([run.results[0].ok, run.results[0].error_code, run.results[0].safe_to_retry], [false, code, safe], JSON.stringify(answer));
    assert.equal(run.requests.filter(r => r.key === OFFER_SEND).length, 1, JSON.stringify(answer));
    assert.equal(run.store['promotion_action-1'].finished, true);
  }
});

// Shapes from the seller's Selling Hub; every value is made up.
const DEPOP = 'webapi.depop.com/presentation/api/v1';
const DEPOP_PREVIEW = `GET ${DEPOP}/offers/products/999/offers/send-all/`;
const DEPOP_SEND = `POST ${DEPOP}/offers/products/999/offers/send-all/jobs/`;
const DEPOP_JOB = `GET ${DEPOP}/offers/products/999/offers/send-all/jobs/offer-job-1/`;
const depopProduct = { slug: SLUG, id: 999, user_id: 55, status: 'STATUS_ONSALE', price_amount: '50.00', price_currency: 'USD' };
const depopPrice = item => ({ price: item, total_estimated_price_amount: '55.00', price_break_down: { item_price: item } });
const depopPreview = (overrides = {}) => ({ product_id: 999, product_description: 'Vintage tee', offer_recipient_id: 4242,
  offer_recipient_username: 'buyer', offer_additional_recipients_count: '2',
  prices: { original_price: depopPrice('50.00'), current_price: depopPrice('50.00') }, price_currency: 'USD', variant_set: 1,
  variant_id: 2, shipping_cost: '5.00', sellers_lowest_offer_value: '25.00', offer_expiration_hours: 48, ...overrides });
const depopAnswers = (overrides = {}) => ({
  [`GET ${DEPOP}/users/me/`]: [200, { id: 55 }],
  [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, depopProduct],
  [DEPOP_PREVIEW]: [200, depopPreview()],
  [DEPOP_SEND]: [200, { job_id: 'offer-job-1' }],
  [DEPOP_JOB]: [200, { status: 'SUCCESS' }],
  ...overrides,
});
const depopOffer = (run, overrides = {}) => run.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'depop',
  action: 'offer', listing_id: SLUG, confirm: true, action_id: 'action-1', price_cents: 5000, offer_price_cents: 4250, floor_cents: 4000, ...overrides } });

test('Depop check reads eligible likers from the send-all preview, without requiring active offers or writing', async () => {
  const check = offerWorker('depop', depopAnswers());
  for (const percent of [15, 60]) {
    await check.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer',
      listing_id: SLUG, discount_percent: percent } });
  }
  // At least 5% off, and never under Depop's lowest allowed offer of $25: at most 50% off.
  assert.deepEqual(check.results[0], { ok: true, price_cents: 5000, like_count: 3, minimum_discount_percent: 5, maximum_discount_percent: 50,
    shipping_discounts: [], offer_price_cents: 4250 });
  assert.equal(check.results[1].offer_price_cents, null);
  assert.ok(check.requests.every(r => r.key.startsWith('GET') && r.headers.Authorization === 'Bearer depop-token'));

  assert.ok(!check.requests.some(r => r.key.includes('/offers/me/products/')));
  const none = offerWorker('depop', depopAnswers({ [DEPOP_PREVIEW]: [400, { message: 'No eligible likers' }] }));
  await none.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer',
    listing_id: SLUG, discount_percent: 15 } });
  assert.equal(none.results[0].ok, false);
  assert.ok(none.requests.every(r => r.key.startsWith('GET')));
  const single = offerWorker('depop', depopAnswers({ [DEPOP_PREVIEW]: [200, depopPreview({ offer_additional_recipients_count: '0' })] }));
  await single.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer',
    listing_id: SLUG, discount_percent: 15 } });
  assert.equal(single.results[0].like_count, 1);
});

test('Depop offer previews read the current product pricing currency and still refuse other currencies', async () => {
  for (const currency of ['USD', 'GBP']) {
    const product = { ...depopProduct, price_currency: undefined, pricing: { currency } };
    const run = offerWorker('depop', depopAnswers({ [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, product] }));
    await run.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer', listing_id: SLUG, discount_percent: 5 } });
    assert.equal(run.results[0].ok, currency === 'USD');
    assert.ok(run.requests.every(r => r.key.startsWith('GET')));
  }
});

test('Depop verifies USD across product and preview without assuming missing currencies', async () => {
  for (const [productCurrency, previewCurrency, ok] of [
    ['USD', undefined, true], [undefined, 'USD', true],
    [undefined, undefined, false], ['GBP', 'USD', false], ['USD', 'GBP', false],
  ]) {
    const run = offerWorker('depop', depopAnswers({
      [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, { ...depopProduct, price_currency: productCurrency }],
      [DEPOP_PREVIEW]: [200, depopPreview({ price_currency: previewCurrency })],
    }));
    await run.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: {
      marketplace: 'depop', action: 'offer', listing_id: SLUG, discount_percent: 10,
    } });
    assert.equal(run.results[0].ok, ok);
    assert.ok(run.requests.every(r => r.key.startsWith('GET')));
  }
});

test('Depop reads the price from the offer preview when its product document has none', async () => {
  // Depop's product document (2026-10-30): no price or currency, sizes under variants_all,
  // and a preview without a lowest offer.
  const bare = { slug: SLUG, id: 999, user_id: 55, status: 'STATUS_ONSALE', variants_all: [{ id: 17, quantity: 1, status: 'STATUS_ONSALE', variant: 'M' }] };
  const preview = depopPreview();
  delete preview.sellers_lowest_offer_value;
  const check = offerWorker('depop', depopAnswers({ [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, bare], [DEPOP_PREVIEW]: [200, preview] }));
  await check.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer',
    listing_id: SLUG, discount_percent: 15 } });
  assert.equal(check.results[0].ok, true, JSON.stringify(check.results[0]));
  assert.equal(check.results[0].price_cents, 5000);
  assert.equal(check.results[0].offer_price_cents, 4250);
  const send = offerWorker('depop', depopAnswers({ [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, bare], [DEPOP_PREVIEW]: [200, preview] }));
  await depopOffer(send);
  assert.equal(send.results[0].ok, true, JSON.stringify(send.results[0]));
  assert.equal(send.requests.filter(r => r.key === DEPOP_SEND).length, 1);
  const sizes = offerWorker('depop', depopAnswers({ [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, { ...bare,
    variants_all: [{ id: 17, quantity: 1, variant: 'M' }, { id: 18, quantity: 1, variant: 'L' }] }], [DEPOP_PREVIEW]: [200, preview] }));
  await depopOffer(sizes);
  assert.equal(sizes.results[0].ok, false);
  assert.ok(!sizes.requests.some(r => r.key === DEPOP_SEND));
  const priceless = offerWorker('depop', depopAnswers({ [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, bare],
    [DEPOP_PREVIEW]: [200, depopPreview({ prices: {} })] }));
  await depopOffer(priceless);
  assert.equal(priceless.results[0].ok, false);
  assert.ok(!priceless.requests.some(r => r.key === DEPOP_SEND));
});

test('Depop says plainly when nobody who liked a listing can get an offer', async () => {
  // Live answers (2026-10-30): no likes is HTTP 400 code 1003; a liker who cannot get an offer leaves the recipient out.
  const noLikes = { id: 'USER_ERROR', message: 'You are unable to submit an offer on this product. Does the product have variants and are you sending them?', code: 1003 };
  const noRecipient = { product_id: 999, product_description: 'Vintage tee', price_currency: 'USD', variant_set: 1, variant_id: 2,
    shipping_cost: '1.99', sellers_lowest_offer_value: '4.46', offer_expiration_hours: 24 };
  for (const answer of [[400, noLikes], [200, noRecipient]]) {
    const run = offerWorker('depop', depopAnswers({ [DEPOP_PREVIEW]: answer }));
    await run.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer',
      listing_id: SLUG, discount_percent: 15 } });
    assert.equal(run.results[0].ok, false);
    assert.match(run.results[0].error, /Nobody who liked this Depop listing can get an offer/, JSON.stringify(run.results[0]));
    const send = offerWorker('depop', depopAnswers({ [DEPOP_PREVIEW]: answer }));
    await depopOffer(send);
    assert.equal(send.results[0].ok, false);
    assert.ok(!send.requests.some(r => r.key === DEPOP_SEND));
  }
});

test('a Depop preview with one liker and no additional count still offers to that liker', async () => {
  const preview = depopPreview();
  delete preview.offer_additional_recipients_count;
  const check = offerWorker('depop', depopAnswers({ [DEPOP_PREVIEW]: [200, preview] }));
  await check.c.handleMarketplacePromotion({ type: 'marketplace.promotion_check', payload: { marketplace: 'depop', action: 'offer',
    listing_id: SLUG, discount_percent: 15 } });
  assert.equal(check.results[0].ok, true, JSON.stringify(check.results[0]));
  assert.equal(check.results[0].like_count, 1);
  const bad = offerWorker('depop', depopAnswers({ [DEPOP_PREVIEW]: [200, depopPreview({ offer_additional_recipients_count: 'many' })] }));
  await depopOffer(bad);
  assert.equal(bad.results[0].ok, false);
  assert.ok(!bad.requests.some(r => r.key === DEPOP_SEND));
});

test('Depop submits one bulk job, waits for success, and never invents a delivered count or repeats the POST', async () => {
  const statuses = ['QUEUED', 'PROCESSING', 'SUCCESS'];
  const sent = offerWorker('depop', depopAnswers({ [DEPOP_JOB]: () => [200, { status: statuses.shift() }] }));
  await depopOffer(sent);
  await depopOffer(sent);
  assert.deepEqual(sent.results[0], { ok: true, state: 'done' });
  assert.deepEqual(sent.results[1], sent.results[0]);
  const posts = sent.requests.filter(r => r.key.startsWith('POST'));
  assert.deepEqual(posts.map(r => [r.key, r.body]), [[DEPOP_SEND, { offer_currency: 'USD', offer_value: '42.50' }]]);
  assert.equal(sent.requests.filter(r => r.key === DEPOP_JOB).length, 3);
  assert.equal(posts[0].headers.Authorization, 'Bearer depop-token');
  assert.equal(posts[0].headers['Content-Type'], 'application/json');
  assert.equal(sent.store['promotion_action-1'].finished, true);
});

test('a Depop bulk job with a failed, lost, malformed or timed out status remains uncertain and never resends', async () => {
  for (const answer of [[200, { status: 'FAILURE' }], [200, { status: 'UNKNOWN' }], [200, {}],
    [401, {}], [429, {}], [502, 'bad gateway']]) {
    const run = offerWorker('depop', depopAnswers({ [DEPOP_JOB]: answer }));
    await depopOffer(run);
    await depopOffer(run);
    assert.deepEqual([run.results[0].ok, run.results[0].error_code, run.results[0].safe_to_retry], [false, 'uncertain', false]);
    assert.equal(run.requests.filter(r => r.key === DEPOP_SEND).length, 1);
    assert.deepEqual(run.results[1], run.results[0]);
  }
  for (const job of [{}, { job_id: '../other' }, { job_id: 123 }]) {
    const run = offerWorker('depop', depopAnswers({ [DEPOP_SEND]: [200, job] }));
    await depopOffer(run);
    assert.deepEqual([run.results[0].error_code, run.results[0].safe_to_retry], ['uncertain', false]);
    assert.ok(!run.requests.some(r => r.key === DEPOP_JOB));
  }
  const timed = offerWorker('depop', depopAnswers({ [DEPOP_JOB]: [200, { status: 'PROCESSING' }] }));
  vm.runInContext('globalThis.testNow = Date.now(); Date = class extends Date { static now() { return globalThis.testNow; } }', timed.c);
  timed.c.marketplaceSleep = async () => { timed.c.testNow += 61000; };
  await depopOffer(timed);
  assert.deepEqual([timed.results[0].error_code, timed.results[0].safe_to_retry], ['uncertain', false]);
  assert.equal(timed.requests.filter(r => r.key === DEPOP_SEND).length, 1);
});

test('the Depop worker completes the bulk job over HTTP and answers duplicates from its journal', async t => {
  const { createServer } = require('node:http');
  const run = offerWorker('depop', {});
  const answers = depopAnswers();
  const seen = [];
  const statuses = ['QUEUED', 'PROCESSING', 'SUCCESS'];
  const server = createServer(async (req, res) => {
    let text = '';
    for await (const part of req) text += part;
    const key = `${req.method} webapi.depop.com${req.url}`;
    seen.push({ key, body: text ? JSON.parse(text) : null });
    const [status, body] = key === DEPOP_JOB ? [200, { status: statuses.shift() }] : answers[key] || [404, {}];
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const origin = `http://127.0.0.1:${server.address().port}`;
  run.c.fetch = (url, options) => fetch(`${origin}${new URL(url).pathname}`, options);
  await depopOffer(run);
  const count = seen.length;
  await depopOffer(run);
  assert.deepEqual(run.results, [{ ok: true, state: 'done' }, { ok: true, state: 'done' }]);
  assert.equal(seen.length, count);
  assert.deepEqual(seen.filter(r => r.key.startsWith('POST')), [{ key: DEPOP_SEND, body: { offer_currency: 'USD', offer_value: '42.50' } }]);
  assert.equal(seen.filter(r => r.key === DEPOP_JOB).length, 3);
});

test('Depop never offers below the floor or its lowest offer, above 5% off, on a changed price, several sizes or someone else’s item', async () => {
  const cases = [
    [{}, { offer_price_cents: 4250, floor_cents: 4500 }],
    [{}, { offer_price_cents: 4800 }],
    [{}, { offer_price_cents: 99, floor_cents: 1 }],
    [{ [DEPOP_PREVIEW]: [200, depopPreview({ sellers_lowest_offer_value: '45.00' })] }, {}],
    [{ [DEPOP_PREVIEW]: [200, depopPreview({ prices: { original_price: depopPrice('50.00'), current_price: depopPrice('40.00') } })] }, {}],
    [{}, { price_cents: 6000 }],
    [{ [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, { ...depopProduct, variants: { 1: { quantity: 1 }, 2: { quantity: 1 } } }] }, {}],
    [{ [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, { ...depopProduct, user_id: 8 }] }, {}],
    [{ [`GET ${DEPOP}/products/by-slug/${SLUG}/`]: [200, { ...depopProduct, status: 'STATUS_SOLD' }] }, {}],
    [{ [DEPOP_PREVIEW]: [400, { message: 'No eligible likers' }] }, {}],
    [{ [DEPOP_PREVIEW]: [200, depopPreview({ offer_recipient_id: null })] }, {}],
    [{ [DEPOP_PREVIEW]: [200, depopPreview({ product_id: 123 })] }, {}],
    [{ [DEPOP_PREVIEW]: [200, depopPreview({ price_currency: 'GBP' })] }, {}],
  ];
  for (const [answers, overrides] of cases) {
    const run = offerWorker('depop', depopAnswers(answers));
    await depopOffer(run, overrides);
    const label = JSON.stringify([Object.keys(answers), overrides]);
    assert.deepEqual([run.results[0].ok, run.results[0].safe_to_retry], [false, true], label);
    assert.ok(!run.requests.some(r => r.key.startsWith('POST')), label);
  }
});

test('a Depop refusal after the send is final; a lost or unreadable reply is uncertain; neither is retried', async () => {
  const cases = [
    [[400, { message: 'An offer is already active' }], 'rejected', true],
    [[429, { message: 'Slow down' }], 'rate_limited', true],
    [[401, { message: 'Unauthorised' }], 'signed_out', true],
    [[409, { message: 'Conflict' }], 'uncertain', false],
    [[502, 'bad gateway'], 'uncertain', false],
    [[200, '<!doctype html><html>Sign in</html>'], 'uncertain', false],
  ];
  for (const [answer, code, safe] of cases) {
    const run = offerWorker('depop', depopAnswers({ [DEPOP_SEND]: answer }));
    await depopOffer(run);
    assert.deepEqual([run.results[0].error_code, run.results[0].safe_to_retry], [code, safe], JSON.stringify(answer));
    assert.equal(run.requests.filter(r => r.key === DEPOP_SEND).length, 1, JSON.stringify(answer));
  }
});

test('Depop has no share action', async () => {
  const run = offerWorker('depop', depopAnswers());
  await run.c.handleMarketplacePromotion({ type: 'marketplace.promote', payload: { marketplace: 'depop', action: 'share', listing_id: SLUG,
    confirm: true, action_id: 'action-2' } });
  assert.deepEqual([run.results[0].ok, run.results[0].error_code], [false, 'rejected']);
  assert.deepEqual(run.requests, []);
});
