const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker, call, reply } = require('./worker-context');

// The item page is modelled on Facebook's Marketplace product page, which embeds
// the listing record as JSON. Not yet compared with a live signed-in page.
const ACCOUNT = '100001234567890';
const LISTING = '1234567890123456';

function itemPage(id, fields = {}, seller = ACCOUNT) {
  const target = {
    __typename: 'GroupCommerceProductItem', id, marketplace_listing_title: 'Secret leather jacket',
    listing_price: { amount: '42.00' }, is_sold: false, is_pending: false, is_live: true, is_hidden: false, is_draft: false,
    marketplace_listing_seller: { __typename: 'User', id: seller, name: 'Pat Seller' }, ...fields,
  };
  const data = { require: [['RelayPrefetchedStreamCache', 'next', [], ['adp_MarketplacePDPContainerQueryRelayPreloader_1',
    { __bbox: { complete: true, result: { data: { viewer: { marketplace_product_details_page: { target } } } } } }]]] };
  return `<html><head><script type="application/json" data-sjs>{"require":[["Bootloader"]]}</script>`
    + `<script type="application/json" data-content-len="1" data-sjs>${JSON.stringify(data)}</script></head><body></body></html>`;
}

function worker({ pages = {}, cookies = { c_user: { value: ACCOUNT }, xs: { value: 'xs-secret' } } } = {}) {
  const c = loadWorker();
  const calls = [];
  const results = [];
  c.chrome.cookies = { get: async ({ name }) => cookies[name] || null };
  c.fetch = async url => {
    calls.push(String(url));
    const id = /\/marketplace\/item\/(\d+)\/$/.exec(String(url))?.[1];
    const page = pages[id];
    if (page === undefined) return reply(404, 'Not found');
    return reply(200, page, { 'content-type': 'text/html; charset=utf-8' });
  };
  c.sendBrowserResult = (_msg, result) => results.push(JSON.parse(JSON.stringify(result)));
  return { c, calls, results };
}

const plain = value => JSON.parse(JSON.stringify(value));

test('a live listing on the seller\'s own account reads as live, without its title, price or seller name', async () => {
  const { c, calls } = worker({ pages: { [LISTING]: itemPage(LISTING) } });
  const result = plain(await c.readFacebookListing(LISTING, Date.now() + 30000));
  assert.deepEqual(result, { ok: true, state: 'live', owner: 'you' });
  assert.deepEqual(calls, [`https://www.facebook.com/marketplace/item/${LISTING}/`]);
  for (const secret of ['Secret leather jacket', '42.00', 'Pat Seller', ACCOUNT]) assert.equal(JSON.stringify(result).includes(secret), false);
});

test('the listing record decides sold, pending, hidden and not live, and names another seller\'s listing', () => {
  const state = fields => call(loadWorker(), 'facebookListingRecord', itemPage(LISTING, fields), LISTING);
  const c = loadWorker();
  const map = fields => vm.runInContext(`facebookState(${JSON.stringify(state(fields))})`, c);
  assert.equal(map({ is_sold: true, is_live: false }), 'sold');
  assert.equal(map({ is_pending: true }), 'pending');
  assert.equal(map({ is_hidden: true }), 'hidden');
  assert.equal(map({ is_live: false }), 'not_live');
  assert.equal(state({}).seller_id, ACCOUNT);
  assert.equal(call(c, 'facebookListingRecord', itemPage('999999999', {}), LISTING), null);
});

test('a listing on another account is reported as someone else\'s', async () => {
  const { c } = worker({ pages: { [LISTING]: itemPage(LISTING, { is_sold: true }, '200002') } });
  assert.deepEqual(plain(await c.readFacebookListing(LISTING, Date.now() + 30000)), { ok: true, state: 'sold', owner: 'other' });
});

test('without a Facebook sign-in in Chrome nothing is requested', async () => {
  const { c, calls, results } = worker({ cookies: {}, pages: { [LISTING]: itemPage(LISTING) } });
  await c.handleMarketplaceListingState({ payload: { marketplace: 'facebook', listing_id: LISTING } });
  assert.equal(calls.length, 0);
  assert.equal(results[0].ok, false);
  assert.equal(results[0].error_code, 'signed_out');
  assert.equal(results[0].safe_to_retry, true);
});

test('a page without the listing is not read as any state; a sign-in or checkpoint page says so', async () => {
  const empty = worker({ pages: { [LISTING]: '<html><script type="application/json">{"a":1}</script></html>' } });
  await assert.rejects(empty.c.readFacebookListing(LISTING, Date.now() + 30000), error => error.code === 'schema_drift');
  const login = worker({ pages: { [LISTING]: '<html><form id="login_form"></form></html>' } });
  await assert.rejects(login.c.readFacebookListing(LISTING, Date.now() + 30000), error => error.code === 'signed_out');
  const checkpoint = worker({ pages: { [LISTING]: '<html><a href="/checkpoint/1501092823525282/">Continue</a></html>' } });
  await assert.rejects(checkpoint.c.readFacebookListing(LISTING, Date.now() + 30000), error => error.code === 'challenge');
});

test('a listing check names one Facebook listing and waits for a running action', async () => {
  const { c, calls, results } = worker({ pages: { [LISTING]: itemPage(LISTING) } });
  await c.handleMarketplaceListingState({ payload: { marketplace: 'poshmark', listing_id: LISTING } });
  await c.handleMarketplaceListingState({ payload: { marketplace: 'facebook', listing_id: '12ab' } });
  vm.runInContext('activeNativeOperation = { id: "busy" }', c);
  await c.handleMarketplaceListingState({ payload: { marketplace: 'facebook', listing_id: LISTING } });
  vm.runInContext('activeNativeOperation = null', c);
  assert.equal(calls.length, 0);
  assert.deepEqual(results.map(result => result.ok), [false, false, false]);
  await c.handleMarketplaceListingState({ payload: { marketplace: 'facebook', listing_id: LISTING } });
  assert.deepEqual(results.at(-1), { ok: true, state: 'live', owner: 'you' });
});

test('a sale pass reads a few listings, reports the seller\'s own sold ones, and never retries a page', async () => {
  const ids = Array.from({ length: 8 }, (_, index) => `${LISTING.slice(0, -1)}${index}`);
  const pages = Object.fromEntries(ids.map(id => [id, itemPage(id)]));
  pages[ids[0]] = itemPage(ids[0], { is_sold: true, is_live: false });
  pages[ids[1]] = itemPage(ids[1], { is_sold: true }, '200002');
  pages[ids[2]] = itemPage(ids[2], { is_pending: true });
  delete pages[ids[3]];
  pages[ids[7]] = itemPage(ids[7], { is_sold: true });
  const { c, calls } = worker({ pages });
  const result = plain(await c.readMarketplaceSales('facebook', ids));
  assert.deepEqual(result, { ok: true, sales: [{ listing_id: ids[0] }] });
  assert.equal(calls.length, 6);
  assert.equal(new Set(calls).size, 6);
});

test('a sign-in page stops the sale pass and the check fails', async () => {
  const ids = [LISTING, '1234567890123457'];
  const { c, calls } = worker({ pages: { [LISTING]: '<html><form id="login_form"></form></html>', [ids[1]]: itemPage(ids[1]) } });
  assert.equal((await c.readMarketplaceSales('facebook', ids)).ok, false);
  assert.equal(calls.length, 1);
});

test('Facebook sign-in comes from Chrome\'s cookies and never shows their values', async () => {
  const later = Math.floor(Date.now() / 1000) + 86400 * 30;
  const { c } = worker({ cookies: { c_user: { value: ACCOUNT, expirationDate: later }, xs: { value: 'xs-secret', expirationDate: later + 10 } } });
  const summary = plain(await c.readExtensionSession('facebook'));
  assert.deepEqual(summary, { state: 'signed_in', expires_at: later, label: null, expiring: false });
  const signedOut = worker({ cookies: { c_user: { value: ACCOUNT } } });
  assert.equal((await signedOut.c.readExtensionSession('facebook')).state, 'signed_out');
  assert.deepEqual(plain(await c.readExtensionSessions()).facebook.state, 'signed_in');
});

test('a recorded Facebook GraphQL form keeps the operation, doc_id and variable shape, never session fields or values', () => {
  const c = loadWorker();
  const raw = new URLSearchParams({
    av: ACCOUNT, __user: ACCOUNT, __a: '1', fb_dtsg: 'NAcsecret-dtsg', jazoest: '25555', lsd: 'lsd-secret',
    fb_api_caller_class: 'RelayModern', fb_api_req_friendly_name: 'MarketplaceListingDeleteMutation',
    variables: JSON.stringify({ input: { listing_id: LISTING, client_mutation_id: '1', actor_id: ACCOUNT } }),
    server_timestamps: 'true', doc_id: '7654321098765432',
  }).toString();
  const body = plain(vm.runInContext(`recordedBody(${JSON.stringify(raw)}, 'application/x-www-form-urlencoded')`, c));
  assert.equal(body.kind, 'graphql-form');
  assert.deepEqual(body.operations, [{ operation: 'MarketplaceListingDeleteMutation', doc_id: '7654321098765432',
    variables: { input: { listing_id: 'string', client_mutation_id: 'string', actor_id: 'string' } } }]);
  assert.ok(body.names.includes('fb_dtsg'));
  for (const secret of ['NAcsecret-dtsg', 'lsd-secret', ACCOUNT, LISTING]) assert.equal(JSON.stringify(body).includes(secret), false);
  const response = plain(vm.runInContext(`recordedResponse(${JSON.stringify('{"data":{"node":{"id":"1"}}}\n{"label":"x","path":[]}')})`, c));
  assert.deepEqual(response, { data: { node: { id: 'string' } } });
  assert.deepEqual(plain(vm.runInContext(`recordedResponse(${JSON.stringify('for (;;);{"payload":1}')})`, c)), { payload: 'number' });
});

test('Facebook item page reads go out as the selling page, with no extension origin, and nothing else is rewritten', async () => {
  const c = loadWorker();
  const updates = [];
  c.chrome.declarativeNetRequest.updateSessionRules = async value => { updates.push(value); };
  await c.ensureMarketplaceRequestContext();
  const rule = updates[0].addRules.find(value => value.id === 17029);
  const target = new RegExp(rule.condition.regexFilter);
  assert.ok(target.test(`https://www.facebook.com/marketplace/item/${LISTING}/`));
  for (const url of ['https://www.facebook.com/api/graphql/', `https://www.facebook.com/marketplace/item/${LISTING}/?ref=x`,
    `https://www.facebook.com.evil/marketplace/item/${LISTING}/`]) assert.equal(target.test(url), false, url);
  assert.deepEqual(plain(rule.condition.requestMethods), ['get']);
  assert.deepEqual(plain(rule.action.requestHeaders), [
    { header: 'Referer', operation: 'set', value: 'https://www.facebook.com/marketplace/you/selling' },
    { header: 'Origin', operation: 'remove' },
  ]);
});
