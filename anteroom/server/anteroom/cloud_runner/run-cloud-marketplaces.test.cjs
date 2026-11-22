'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { executeMarketplaceOperation } = require('./marketplace-runner.cjs');

const depopCookie = { name: 'access_token', value: 'test-token', domain: 'www.depop.com', secure: true };
const mercariCookie = { name: 'session', value: 'test-session', domain: 'www.mercari.com', secure: true };
const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});

for (const marketplace of ['mercari', 'depop']) {
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

test('Mercari verifies through initialize and imports a page with its own session', async () => {
  const calls = [];
  const httpFetch = async (url, options) => {
    assert.equal(options.headers.Cookie, url.endsWith('/initialize') ? 'session=test-session' : 'session=rotated');
    assert.equal(options.headers.Origin, 'https://www.mercari.com');
    calls.push(url);
    if (url.endsWith('/initialize')) {
      return new Response(JSON.stringify({ csrf: 'csrf', user: { userId: 42, name: 'Seller' } }), {
        headers: { 'content-type': 'application/json', 'set-cookie': 'session=rotated; Path=/; Secure' },
      });
    }
    const body = JSON.parse(options.body);
    assert.equal(body.operationName, 'publicProfileSearchQuery');
    assert.equal(body.variables.criteria.offset, 24);
    assert.deepEqual(body.variables.criteria.sellerIds, [42]);
    return json({ data: { search: { itemsList: [{ id: 'm123456', status: 'on_sale', name: 'Coat', price: 5000 }] } } });
  };
  const execute = (type, payload) => executeMarketplaceOperation({
    marketplace: 'mercari', cookies: [mercariCookie, depopCookie], command: { type, payload: { marketplace: 'mercari', ...payload } }, httpFetch,
  });
  const verified = await execute('marketplace.verify_session');
  assert.equal(verified.result.session.state, 'signed_in');
  assert.equal(verified.result.session.label, 'Seller');
  assert.equal(verified.cookies_changed, true);
  assert.deepEqual(verified.cookies.map(({ name, value }) => [name, value]), [['session', 'rotated']]);
  const imported = await execute('marketplace.import.page', { cursor: '24' });
  assert.equal(imported.result.ok, true);
  assert.equal(imported.result.account, '42');
  assert.equal(imported.result.rows[0].id, 'm123456');
  assert.equal(calls.length, 3);
});

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
