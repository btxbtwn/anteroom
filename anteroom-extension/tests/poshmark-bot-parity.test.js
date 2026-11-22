const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

const USER = 'a'.repeat(24);
const POST = 'b'.repeat(24);
const OTHER_POST = 'd'.repeat(24);
const DISCOUNT = 'c'.repeat(24);
const jwt = `x.${Buffer.from(JSON.stringify({ user_id: USER })).toString('base64')}.y`;
const PAGE = `<html><script>window.__INITIAL_STATE__={"csrftoken":"page-csrf","userCookies":{"userInfo":{"uid":"${USER}"}}};</script></html>`;
const livePost = {
  data: {
    creator_id: USER,
    status: 'published',
    price_amount: { val: '50', currency_code: 'USD' },
    like_count: 3,
    inventory: { status: 'available', size_quantities: [{ quantity_sold: 0 }] },
  },
};

function setupWorker(routes = {}) {
  const c = loadWorker();
  const store = {};
  c.chrome.cookies = { get: async ({ name }) => ({ jwt: { value: jwt } })[name] };
  c.chrome.storage = {
    local: {
      get: async key => (key in store ? { [key]: structuredClone(store[key]) } : {}),
      set: async values => { for (const [k, v] of Object.entries(values)) store[k] = structuredClone(v); },
    },
  };
  const requests = [];
  const bodies = {};
  const allRoutes = {
    'GET poshmark.com/create-listing': [200, PAGE, { 'content-type': 'text/html' }],
    [`GET poshmark.com/vm-rest/posts/${POST}`]: [200, livePost],
    ...routes,
  };
  c.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    const key = `${options.method || 'GET'} ${parsed.hostname}${parsed.pathname}`;
    requests.push({ key, search: parsed.search });
    if (options.body) bodies[key] = JSON.parse(options.body);
    const answer = allRoutes[key];
    const [status, body, headers] = typeof answer === 'function' ? answer(parsed) : answer || [404, {}];
    return reply(status, body, headers);
  };
  const results = [];
  c.sendBrowserResult = (_msg, result) => results.push(JSON.parse(JSON.stringify(result)));
  return { c, requests, bodies, results };
}

test('Posh Party share sends event_id to active party when party window is active', async () => {
  const { c, requests, results } = setupWorker({
    'GET poshmark.com/vm-rest/parties': [200, { data: [{ id: 'party-9am', status: 'active' }] }],
    [`PUT poshmark.com/vm-rest/users/self/shared_posts/${POST}`]: [200, { data: {} }],
  });
  await c.handleMarketplacePromotion({
    type: 'marketplace.promote',
    payload: {
      marketplace: 'poshmark',
      action: 'share',
      listing_id: POST,
      confirm: true,
      action_id: 'party-share-1',
      share_modes: ['party'],
      party_window: '09:00',
    },
  });
  assert.equal(results[0].ok, true);
  assert.equal(results[0].share_mode, 'party');
  const partyReq = requests.find(r => r.key === `PUT poshmark.com/vm-rest/users/self/shared_posts/${POST}`);
  assert.ok(partyReq);
  assert.match(partyReq.search, /event_id=party-9am/);
});

test('Poshmark Community return-shares share recent sharers items', async () => {
  const { c, requests, results } = setupWorker({
    [`GET poshmark.com/vm-rest/users/${USER}/newsfeed`]: [
      200,
      { data: [{ actor_id: 'other-seller-1', return_post_id: OTHER_POST }] },
    ],
    [`PUT poshmark.com/vm-rest/users/self/shared_posts/${OTHER_POST}`]: [200, { data: {} }],
  });
  await c.handleMarketplacePromotion({
    type: 'marketplace.promote',
    payload: {
      marketplace: 'poshmark',
      action: 'share',
      listing_id: '__account__',
      share_mode: 'community',
      max_shares: 5,
      confirm: true,
      action_id: 'comm-share-1',
    },
  });
  assert.equal(results[0].ok, true);
  assert.equal(results[0].share_mode, 'community');
  assert.equal(results[0].recipient_count, 1);
  assert.ok(requests.some(r => r.key === `PUT poshmark.com/vm-rest/users/self/shared_posts/${OTHER_POST}`));
});

test('Poshmark private bundle offer waits for like pause, enforces min items, and sends first comment', async () => {
  const oldLike = new Date(Date.now() - 30 * 60 * 1000).toISOString();
  const { c, bodies, results } = setupWorker({
    [`GET poshmark.com/vm-rest/posts/${POST}/likes`]: [
      200,
      { data: [{ user_id: 'buyer-1', username: 'happybuyer', created_at: oldLike }] },
    ],
    [`POST poshmark.com/vm-rest/users/${USER}/bundles`]: [
      200,
      { data: { bundle: { id: 'bundle-77', item_count: 1, posts: [{ id: POST }], is_first_bundle: true, comment_count: 0 } } },
    ],
    'POST poshmark.com/vm-rest/bundles/bundle-77/offers': [200, { data: {} }],
    'POST poshmark.com/vm-rest/bundles/bundle-77/comments': [200, { data: {} }],
  });
  await c.handleMarketplacePromotion({
    type: 'marketplace.promote',
    payload: {
      marketplace: 'poshmark',
      action: 'bundle_offer',
      listing_id: POST,
      confirm: true,
      action_id: 'bundle-1',
      price_cents: 5000,
      offer_price_cents: 4000,
      floor_cents: 3500,
      shipping_discount_id: DISCOUNT,
      bundle_pause_minutes: 15,
      bundle_min_items: 1,
      bundle_comment: 'Welcome! Enjoy 20% off your bundle.',
    },
  });
  assert.equal(results[0].ok, true);
  assert.equal(results[0].bundle_id, 'bundle-77');
  assert.equal(bodies['POST poshmark.com/vm-rest/bundles/bundle-77/comments'].comment, 'Welcome! Enjoy 20% off your bundle.');
});

test('Poshmark refuses to apply a single reviewed price to a multi-item bundle', async () => {
  const oldLike = new Date(Date.now() - 30 * 60 * 1000).toISOString();
  const { c, bodies, results } = setupWorker({
    [`GET poshmark.com/vm-rest/posts/${POST}/likes`]: [
      200,
      { data: [{ user_id: 'buyer-1', username: 'happybuyer', created_at: oldLike }] },
    ],
    [`POST poshmark.com/vm-rest/users/${USER}/bundles`]: [
      200,
      { data: { bundle: { id: 'bundle-77', item_count: 2, is_first_bundle: true, comment_count: 0 } } },
    ],
    'POST poshmark.com/vm-rest/bundles/bundle-77/offers': [200, { data: {} }],
    'POST poshmark.com/vm-rest/bundles/bundle-77/comments': [200, { data: {} }],
  });
  await c.handleMarketplacePromotion({
    type: 'marketplace.promote',
    payload: {
      marketplace: 'poshmark',
      action: 'bundle_offer',
      listing_id: POST,
      confirm: true,
      action_id: 'bundle-1',
      price_cents: 5000,
      offer_price_cents: 4000,
      floor_cents: 3500,
      shipping_discount_id: DISCOUNT,
      bundle_pause_minutes: 15,
      bundle_min_items: 2,
      bundle_comment: 'Welcome! Enjoy 20% off your bundle.',
    },
  });
  assert.equal(results[0].ok, false);
  assert.equal(bodies['POST poshmark.com/vm-rest/bundles/bundle-77/offers'], undefined);
});

test('Poshmark automatic follows runs reciprocal 24h, active sellers, unfollows, and opt-in live show unsubscribe', async () => {
  const recent = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
  const { c, requests, bodies, results } = setupWorker({
    [`GET poshmark.com/vm-rest/users/${USER}/newsfeed`]: [200, { data: [{ actor_id: 'follower-1', created_at: recent }] }],
    'PUT poshmark.com/vm-rest/users/self/followed_users/follower-1': [200, { data: {} }],
    'GET poshmark.com/vm-rest/users/active': [200, { data: [{ id: 'active-seller-2' }] }],
    'PUT poshmark.com/vm-rest/users/self/followed_users/active-seller-2': [200, { data: {} }],
    [`GET poshmark.com/vm-rest/users/${USER}/following`]: [200, { data: [{ id: 'old-follow-3' }] }],
    'DELETE poshmark.com/vm-rest/users/self/followed_users/old-follow-3': [200, { data: {} }],
    'PUT poshmark.com/vm-rest/users/self/notification_settings': [200, { data: {} }],
  });
  await c.handleMarketplacePromotion({
    type: 'marketplace.promote',
    payload: {
      marketplace: 'poshmark',
      action: 'follow',
      listing_id: '__account__',
      confirm: true,
      action_id: 'follow-run-1',
      follow_config: {
        reciprocal_24h: true,
        new_active_sellers_cap: 10,
        unfollow_cap: 10,
        unsubscribe_live_shows: true,
      },
    },
  });
  assert.equal(results[0].ok, true);
  assert.equal(results[0].followed_count, 2);
  assert.equal(results[0].unfollowed_count, 1);
  assert.equal(results[0].unsubscribed_live_shows, true);
  assert.equal(bodies['PUT poshmark.com/vm-rest/users/self/notification_settings'].live_show_notifications, false);
  assert.ok(requests.some(r => r.key === 'DELETE poshmark.com/vm-rest/users/self/followed_users/old-follow-3'));
});
