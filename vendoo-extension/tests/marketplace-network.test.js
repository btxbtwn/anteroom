const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

test('Depop request context is limited to this extension and the supported API paths', async () => {
  const c = loadWorker();
  const updates = [];
  c.chrome.declarativeNetRequest.updateSessionRules = async value => { updates.push(value); };
  await c.ensureMarketplaceRequestContext();
  await c.ensureMarketplaceRequestContext();
  assert.equal(updates.length, 1);
  const rule = updates[0].addRules.find(value => value.id === 17001);
  assert.deepEqual([...rule.condition.initiatorDomains], ['test-extension-id']);
  assert.deepEqual([...rule.condition.resourceTypes], ['xmlhttprequest']);
  const target = new RegExp(rule.condition.regexFilter);
  assert.ok(target.test('https://webapi.depop.com/presentation/api/v1/drafts/'));
  for (const url of ['https://webapi.depop.com.evil/presentation/api/v1/drafts/', 'https://webapi.depop.com/api/payments/', 'https://example.com/']) {
    assert.equal(target.test(url), false);
  }
  assert.ok(rule.action.requestHeaders.every(header => ['origin', 'referer'].includes(header.header.toLowerCase())));
  assert.ok(target.test('https://webapi.depop.com/presentation/api/v1/products/by-slug/shop-classic-tee-136d/'));
  assert.ok(target.test('https://webapi.depop.com/presentation/api/v1/shops/29809510/products/by-status/sold/?limit=24'));
  assert.equal(target.test('https://webapi.depop.com/presentation/api/v1/shops/29809510/followers/'), false);
  const receipts = updates[0].addRules.find(value => value.id === 17004);
  const receiptsTarget = new RegExp(receipts.condition.regexFilter);
  assert.ok(receiptsTarget.test('https://webapi.depop.com/api/v1/receipts/?role=seller&limit=24'));
  assert.equal(target.test('https://webapi.depop.com/api/v1/receipts/?role=seller'), false);
  assert.ok(target.test('https://webapi.depop.com/api/v1/addresses/?providers=USPS'));
  assert.ok(target.test('https://webapi.depop.com/api/v1/shipping-providers/?ids=USPS&itemCurrency=USD'));
  assert.ok(target.test('https://webapi.depop.com/presentation/api/v1/addresses/'));
  assert.ok(target.test('https://webapi.depop.com/presentation/api/v1/users/me/settings/shipping/'));
  assert.ok(target.test('https://webapi.depop.com/presentation/api/v1/shipping/providers/depop/?ids=USPS'));
  assert.ok(target.test('https://webapi.depop.com/presentation/api/v1/attributes/'));
  assert.equal(receiptsTarget.test('https://webapi.depop.com/api/payments/'), false);
});

test('Mercari request context covers only its GraphQL and session endpoints from this extension', async () => {
  const c = loadWorker();
  const updates = [];
  c.chrome.declarativeNetRequest.updateSessionRules = async value => { updates.push(value); };
  await c.ensureMarketplaceRequestContext();
  const rule = updates[0].addRules.find(value => value.id === 17002);
  assert.deepEqual([...rule.condition.initiatorDomains], ['test-extension-id']);
  assert.deepEqual([...rule.condition.requestMethods], ['get', 'post']);
  const target = new RegExp(rule.condition.regexFilter);
  assert.ok(target.test('https://www.mercari.com/v1/api'));
  assert.ok(target.test('https://www.mercari.com/v1/initialize'));
  for (const url of ['https://www.mercari.com/v1/api/other', 'https://www.mercari.com.evil/v1/api', 'https://www.mercari.com/mypage/']) {
    assert.equal(target.test(url), false);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(rule.action.requestHeaders.map(header => [header.header, header.value]))),
    [['Origin', 'https://www.mercari.com'], ['Referer', 'https://www.mercari.com/sell/']]);
});

test('a failed header-rule setup is tried again instead of being kept', async () => {
  const c = loadWorker();
  let calls = 0;
  c.chrome.declarativeNetRequest.updateSessionRules = async () => { calls += 1; if (calls === 1) throw new Error('rule quota'); };
  await assert.rejects(() => c.ensureMarketplaceRequestContext(), error => error.code === 'network');
  await c.ensureMarketplaceRequestContext();
  assert.equal(calls, 2);
});

test('Depop\'s accelerated photo bucket gets its web referrer, for this extension\'s uploads only', async () => {
  const c = loadWorker();
  const updates = [];
  c.chrome.declarativeNetRequest.updateSessionRules = async value => { updates.push(value); };
  await c.ensureMarketplaceRequestContext();
  const rule = updates[0].addRules.find(value => value.id === 17006);
  assert.deepEqual([...rule.condition.requestMethods], ['put']);
  assert.deepEqual([...rule.condition.initiatorDomains], ['test-extension-id']);
  const target = new RegExp(rule.condition.regexFilter);
  assert.ok(target.test('https://media-photos-depop.s3-accelerate.amazonaws.com/p/1.jpg'));
  assert.equal(target.test('https://antengo-listings.s3.amazonaws.com/a'), false);
});

test('Sellwild request context covers a seller shop search from this extension', async () => {
  const c = loadWorker();
  const updates = [];
  c.chrome.declarativeNetRequest.updateSessionRules = async value => { updates.push(value); };
  await c.ensureMarketplaceRequestContext();
  const rule = updates[0].addRules.find(value => value.id === 17003);
  const target = new RegExp(rule.condition.regexFilter);
  assert.ok(target.test('https://sellwild.com/api/search/listings?userId=99&limit=200&page=1'));
  assert.ok(target.test('https://sellwild.com/api/listings/105101406'));
  assert.equal(target.test('https://sellwild.com/api/users/99'), false);
  assert.equal(target.test('https://sellwild.com.evil/api/search/listings'), false);
});
