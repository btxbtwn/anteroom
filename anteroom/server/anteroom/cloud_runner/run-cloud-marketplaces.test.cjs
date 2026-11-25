'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { executeMarketplaceOperation } = require('./marketplace-runner.cjs');

const depopCookie = { name: 'access_token', value: 'test-token', domain: 'www.depop.com', secure: true };
const mercariCookie = { name: 'session', value: 'test-session', domain: 'www.mercari.com', secure: true };
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

for (const marketplace of ['depop']) {
  test(`${marketplace} writes require explicit confirmation and a matching marketplace`, async () => {
    const httpFetch = () => assert.fail('Unexpected request');
    for (const [type, action] of [
      ['marketplace.execute', 'publish'],
      ['marketplace.execute', 'update'],
      ['marketplace.delete', undefined],
      ['marketplace.promote', 'offer'],
    ]) {
      const command = { type, payload: { marketplace, action, confirm: 'true' } };
      const { result } = await executeMarketplaceOperation({ marketplace, command, httpFetch });
      assert.equal(result.error_code, 'rejected');
      command.payload.confirm = true;
      command.payload.marketplace = 'poshmark';
      const wrongMarket = await executeMarketplaceOperation({ marketplace, command, httpFetch });
      assert.equal(wrongMarket.result.error_code, 'rejected');
    }
  });
}

// Mercari answers server requests with a Cloudflare challenge, so it runs only in Chrome.
for (const type of ['marketplace.verify_session', 'marketplace.sales_history', 'marketplace.delete', 'marketplace.promotion_check']) {
  test(`the cloud runner refuses Mercari ${type} before any request`, async () => {
    const { result, cookies_changed: cookiesChanged } = await executeMarketplaceOperation({
      marketplace: 'mercari', cookies: [mercariCookie],
      command: { type, payload: { marketplace: 'mercari', listing_id: 'm123456', confirm: true } },
      httpFetch: () => assert.fail('Unexpected request'),
    });
    assert.deepEqual(result, { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Unsupported cloud marketplace.' });
    assert.equal(cookiesChanged, false);
  });
}

// Cloudflare can mark a challenge only in its header. That is a browser check,
// not a refused session: the marketplace is asked once, and the saved cookies stay as they were.
for (const type of ['marketplace.like_events', 'marketplace.verify_session']) {
  for (const status of [401, 403]) {
    test(`Depop cloud ${type} stops on a header-only Cloudflare challenge (HTTP ${status})`, async () => {
      const calls = [];
      const { result, cookies_changed: cookiesChanged, cookies } = await executeMarketplaceOperation({
        marketplace: 'depop', cookies: [depopCookie],
        command: { type, payload: { marketplace: 'depop' } },
        httpFetch: async url => {
          calls.push(new URL(url).pathname);
          return new Response('<!doctype html><html><body>private-response</body></html>', {
            status, headers: { 'content-type': 'text/html', 'cf-mitigated': 'challenge' },
          });
        },
      });
      assert.equal(calls.length, 1, calls.join(', '));
      assert.equal(result.ok, false);
      assert.equal(result.error_code, 'challenge');
      assert.equal(cookiesChanged, false);
      assert.deepEqual(cookies.map(({ name, value }) => [name, value]), [['access_token', 'test-token']]);
    });
  }
}

test('Depop verifies the account and polls likes with its bearer', async () => {
  const execute = (type, httpFetch) => executeMarketplaceOperation({
    marketplace: 'depop', cookies: [depopCookie, mercariCookie],
    command: { type, payload: { marketplace: 'depop' } }, httpFetch,
  });
  const verified = await execute('marketplace.verify_session', async (url, options) => {
    assert.equal(url, 'https://webapi.depop.com/presentation/api/v1/users/me/');
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    assert.equal(options.headers.Cookie, undefined);
    return json({ id: 12, username: 'seller' });
  });
  assert.equal(verified.result.session.state, 'signed_in');
  assert.equal(verified.result.session.label, 'seller');
  const likes = await execute('marketplace.like_events', async (url, options) => {
    assert.match(url, /\/shops\/me\/products\/likes\/$/);
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    return json({ objects: [{ product: { slug: 'seller-coat' }, liked_at: '2026-11-22T01:00:00Z' }] });
  });
  assert.equal(likes.result.ok, true);
  assert.equal(likes.result.events[0].listing_id, 'seller-coat');
});

test('Depop delists only after confirmation and verifies removal', async () => {
  const requests = [];
  const httpFetch = async (url, options) => {
    requests.push([options.method, new URL(url).pathname]);
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    if (options.method === 'DELETE') return json({});
    return requests.length === 1 ? json({ id: 12, status: 'STATUS_ON_SALE' }) : json({}, 404);
  };
  const { result } = await executeMarketplaceOperation({
    marketplace: 'depop', cookies: [depopCookie], httpFetch,
    command: { type: 'marketplace.delete', payload: { marketplace: 'depop', listing_id: 'seller-coat', confirm: true } },
  });
  assert.equal(result.state, 'deleted');
  assert.deepEqual(requests, [
    ['GET', '/presentation/api/v1/products/by-slug/seller-coat/'],
    ['DELETE', '/presentation/api/v1/products/12/'],
    ['GET', '/presentation/api/v1/products/by-slug/seller-coat/'],
  ]);
});

test('Depop cloud auto-relist keeps the old listing when its drafts session is refused', async () => {
  const requests = [];
  const { result } = await executeMarketplaceOperation({
    marketplace: 'depop', cookies: [depopCookie],
    command: {
      type: 'marketplace.delete',
      payload: { marketplace: 'depop', listing_id: 'seller-coat', confirm: true, action: 'relist' },
    },
    httpFetch: async (url, options) => {
      requests.push([options.method, new URL(url).pathname]);
      return json({ message: 'Unauthorized' }, 401);
    },
  });
  assert.equal(result.error_code, 'signed_out');
  assert.deepEqual(requests, [['GET', '/presentation/api/v1/drafts/']]);
});

test('Depop cloud checks relist shipping with read-only requests', async () => {
  const requests = [];
  const { result } = await executeMarketplaceOperation({
    marketplace: 'depop', cookies: [depopCookie],
    command: { type: 'marketplace.form', payload: {
      marketplace: 'depop', check_relist_shipping: true,
      listing: { price: 10.8, direct_category_ids: { depop: 'womenswear_jeans' },
        depop_specifics: { parcelSize: 'Extra extra small' } },
    } },
    httpFetch: async (url, options) => {
      const { pathname, searchParams } = new URL(url);
      requests.push(options.method);
      if (pathname.endsWith('/attributes/')) return json({
        department: [{ id: 'womenswear' }],
        group: [{ id: 'bottoms', department: ['womenswear'], product_types: [{ id: 'jeans' }] }],
        generic_attributes: [],
      });
      if (pathname.endsWith('/size-mapping/')) return json({ category_size_mapping: [], size_sets: [] });
      if (pathname.endsWith('/addresses/')) return json([{ id: 8, country_code: 'US' }]);
      assert.equal(pathname, '/presentation/api/v1/shipping/providers/depop/');
      assert.equal(searchParams.get('item_cost'), '10.80');
      assert.equal(searchParams.get('product_type'), 'jeans');
      return json([{ id: 'USPS', parcel_sizes: [{ id: 'under_8oz', title: 'Extra small' }] }]);
    },
  });
  assert.equal(result.ok, false);
  assert.equal(result.error_code, 'missing_fields');
  assert.match(result.error, /does not offer Extra extra small/);
  assert.deepEqual(requests, ['GET', 'GET', 'GET', 'GET']);
});
