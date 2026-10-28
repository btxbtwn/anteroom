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
  const rules = updates[0].addRules.filter(value => value.action.requestHeaders.some(header => header.value === 'https://www.depop.com/sellinghub/drafts/'));
  const rule = rules.find(value => value.id === 17001);
  assert.deepEqual([...rule.condition.initiatorDomains], ['test-extension-id']);
  assert.deepEqual([...rule.condition.resourceTypes], ['xmlhttprequest']);
  const covered = url => rules.some(value => new RegExp(value.condition.regexFilter).test(url));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/drafts/'));
  for (const url of ['https://webapi.depop.com.evil/presentation/api/v1/drafts/', 'https://webapi.depop.com/api/payments/', 'https://example.com/']) {
    assert.equal(covered(url), false, url);
  }
  assert.ok(rule.action.requestHeaders.every(header => ['origin', 'referer'].includes(header.header.toLowerCase())));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/products/by-slug/shop-classic-tee-136d/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/shops/29809510/products/by-status/sold/?limit=24'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/shops/29809510/products/by-status/selling/?limit=24'));
  assert.ok(covered('https://webapi.depop.com/api/v1/shop/29809510/products/?limit=200'));
  assert.equal(covered('https://webapi.depop.com/api/v1/products/?state=selling&limit=24'), false);
  assert.equal(covered('https://webapi.depop.com/api/v2/shop/29809510/products/?limit=24'), false);
  assert.equal(covered('https://webapi.depop.com/presentation/api/v1/shops/29809510/products/by-status/deleted/'), false);
  assert.equal(covered('https://webapi.depop.com/api/v1/payments/'), false);
  assert.equal(covered('https://webapi.depop.com/presentation/api/v1/shops/29809510/followers/'), false);
  const receipts = updates[0].addRules.find(value => value.id === 17004);
  const receiptsTarget = new RegExp(receipts.condition.regexFilter);
  assert.ok(receiptsTarget.test('https://webapi.depop.com/api/v1/receipts/?role=seller&limit=24'));
  assert.equal(covered('https://webapi.depop.com/api/v1/receipts/?role=seller'), false);
  assert.ok(covered('https://webapi.depop.com/api/v1/addresses/?providers=USPS'));
  assert.ok(covered('https://webapi.depop.com/api/v1/shipping-providers/?ids=USPS&itemCurrency=USD'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/addresses/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/users/me/settings/shipping/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/shipping/providers/depop/?ids=USPS'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/attributes/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/attributes/categories/size-mapping/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/offers/me/products/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/offers/products/999/offers/send-all/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/offers/products/999/offers/send-all/jobs/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/offers/products/999/offers/send-all/jobs/offer-job-1/'));
  assert.ok(covered('https://webapi.depop.com/presentation/api/v1/products/999/offers/'));
  assert.equal(receiptsTarget.test('https://webapi.depop.com/api/payments/'), false);
  assert.ok(rules.every(value => value.condition.regexFilter.length < 200));
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

test('Vinted request context covers the signed-in read and delete, and nothing else', async () => {
  const c = loadWorker();
  const updates = [];
  c.chrome.declarativeNetRequest.updateSessionRules = async value => { updates.push(value); };
  await c.ensureMarketplaceRequestContext();
  const rule = updates[0].addRules.find(value => value.id === 17024);
  assert.deepEqual([...rule.condition.initiatorDomains], ['test-extension-id']);
  assert.deepEqual([...rule.condition.requestMethods], ['get', 'post']);
  assert.ok(rule.condition.regexFilter.length < 200);
  const target = new RegExp(rule.condition.regexFilter);
  for (const url of [
    'https://www.vinted.com/items/new',
    'https://www.vinted.com/web/api/auth/refresh',
    'https://www.vinted.com/api/v2/users/current',
    'https://www.vinted.com/api/v2/items/42',
    'https://www.vinted.com/api/v2/items/42/delete',
  ]) assert.ok(target.test(url), url);
  for (const url of [
    'https://www.vinted.com/api/v2/items/42/edit',
    'https://www.vinted.com.evil/api/v2/items/42',
    'https://vinted.com/api/v2/items/42/delete',
    'https://www.vinted.com/api/v2/item_upload/items',
  ]) assert.equal(target.test(url), false, url);
  assert.deepEqual(JSON.parse(JSON.stringify(rule.action.requestHeaders.map(header => [header.header, header.value]))),
    [['Origin', 'https://www.vinted.com'], ['Referer', 'https://www.vinted.com/']]);
});

test('Vinted draft, photo, wardrobe and package-size calls each have their own request context', async () => {
  const c = loadWorker();
  const updates = [];
  c.chrome.declarativeNetRequest.updateSessionRules = async value => { updates.push(value); };
  await c.ensureMarketplaceRequestContext();
  const rules = Object.fromEntries(updates[0].addRules.map(rule => [rule.id, rule]));
  assert.deepEqual(JSON.parse(JSON.stringify(updates[0].removeRuleIds.filter(id => id >= 17025))), [17025, 17026, 17027, 17028, 17029]);
  const cases = [
    [17025, [
      'https://www.vinted.com/api/v2/item_upload/catalogs',
      'https://www.vinted.com/api/v2/item_upload/attributes',
      'https://www.vinted.com/api/v2/item_upload/size_groups?catalog_ids[]=1',
      'https://www.vinted.com/api/v2/item_upload/brands?keyword=Levi',
      'https://www.vinted.com/api/v2/item_upload/colors',
      'https://www.vinted.com/api/v2/item_upload/drafts',
      'https://www.vinted.com/api/v2/item_upload/drafts/42',
      'https://www.vinted.com/api/v2/item_upload/drafts/42/completion',
      'https://www.vinted.com/api/v2/item_upload/items/42',
    ], [
      'https://www.vinted.com.evil/api/v2/item_upload/drafts',
      'https://www.vinted.com/api/v2/photos',
    ]],
    [17026, ['https://www.vinted.com/api/v2/photos'], ['https://www.vinted.com/api/v2/photos/1']],
    [17027, ['https://www.vinted.com/api/v2/wardrobe/7/items?page=1'], ['https://www.vinted.com/api/v2/wardrobe/items']],
    [17028, ['https://api.www.vinted.com/shipping-estimation/external/catalogs/1904/package_sizes'],
      ['https://api.www.vinted.com/shipping-estimation/external/catalogs/package_sizes']],
  ];
  for (const [id, allowed, rejected] of cases) {
    const rule = rules[id];
    assert.ok(rule.condition.regexFilter.length < 500, rule.condition.regexFilter);
    assert.deepEqual([...rule.condition.initiatorDomains], ['test-extension-id']);
    const target = new RegExp(rule.condition.regexFilter);
    for (const url of allowed) assert.ok(target.test(url), `${id} ${url}`);
    for (const url of rejected) assert.equal(target.test(url), false, `${id} ${url}`);
  }
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
