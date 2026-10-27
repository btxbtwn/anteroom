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
