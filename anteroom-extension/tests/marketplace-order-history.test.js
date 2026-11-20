const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

// Response shapes follow the seller's live accounts on 2026-11-02. Values are made up.
function worker(routes, cookies = {}) {
  const c = loadWorker();
  Object.assign(c, { AbortSignal });
  c.chrome.declarativeNetRequest.updateSessionRules = async () => {};
  c.chrome.cookies = { get: async ({ name }) => (cookies[name] ? { value: cookies[name] } : null) };
  const calls = [];
  c.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    const key = options.body ? `${url} ${options.body}` : String(url);
    const route = Object.entries(routes).find(([part]) => key.includes(part));
    if (!route) return { ok: false, status: 404, headers: { get: () => '' }, text: async () => '', json: async () => null };
    const [status, body] = typeof route[1] === 'function' ? route[1](key) : route[1];
    const text = typeof body === 'string' ? body : JSON.stringify(body);
    return { ok: status === 200, status, statusText: '', headers: { get: () => (typeof body === 'string' ? 'text/html' : 'application/json') },
      json: async () => JSON.parse(text), text: async () => text };
  };
  return { c, calls };
}

const read = async (c, marketplace, settled = []) => JSON.parse(JSON.stringify(await c.readMarketplaceOrderHistory(marketplace, settled)));

test('sales reads classify a refused Depop session', async () => {
  const { c } = worker({ 'receipts/?role=seller': [401, {}] }, { access_token: 'expired' });
  assert.equal((await read(c, 'depop')).error, 'signed_out');
});

test('sales reads retry a transient Depop failure', async () => {
  let attempts = 0;
  const { c } = worker({ 'receipts/?role=seller': () => ++attempts === 1
    ? [503, {}] : [200, { objects: [], page_info: { has_more: false } }] }, { access_token: 'token' });
  assert.equal((await read(c, 'depop')).ok, true);
  assert.equal(attempts, 2);
});

test('Mercari sales start with a fresh session rather than a cached account', async () => {
  const { c } = worker({});
  let refreshed;
  c.mercariCurrentSession = async (_deadline, fresh) => { refreshed = fresh; return { user: { userId: 1 } }; };
  c.mercariRequest = async () => ({ userItems: { items: [], pagination: { hasNext: false } } });
  assert.equal((await read(c, 'mercari')).ok, true);
  assert.equal(refreshed, true);
});

test('unreadable money stays unknown instead of becoming zero', () => {
  const { c } = worker({});
  for (const value of [null, undefined, '', '$', ' ', [], {}, true]) {
    assert.equal(c.historyMoney(value), null);
  }
  assert.equal(c.historyMoney('$1,234.50'), '1234.50');
  assert.equal(c.historyMoney(0), '0.00');
  assert.equal(c.mercariSellerSummary([
    { title: 'Sold price', formattedPrice: '$', style: 'price' },
    { title: 'You made', formattedPrice: '$0', style: 'total' },
  ]), null);
});

const depopReceipt = {
  purchase_id: 479150329953, status: 'shipping_pending', sold_timestamp: '2026-10-29T17:50:44.543494Z',
  buyer: { username: 'buyer' }, shipping_details: { ship_to: { name: 'Buyer' }, buyer_shipping_amount: '1.99', seller_shipping_amount: '0.00' },
  items_amount: '8.00', seller_amount: '6.23', payment_fee: '0.81', fee: '2.06',
  fee_breakdown: { seller_fee: { amount: '0.00' }, boosting_fee: { amount: '0.96' } },
  payout: { status: 'PENDING', amount: '6.23' },
  lines: [{ id: 352688723, product_id: 952073473, slug: 'shop-sports-bra-90de', product_amount: '8.00' }],
};

test('Depop reads each unsettled receipt with the seller amounts and no buyer details', async () => {
  const { c, calls } = worker({
    'receipts/?role=seller': [200, { objects: [{ purchase_id: 479150329953 }, { purchase_id: 111 }],
      page_info: { has_more: false, last: null } }],
    'receipts/479150329953/': [200, depopReceipt],
  }, { access_token: 'token' });
  const result = await read(c, 'depop', ['111']);
  assert.equal(result.ok, true);
  assert.deepEqual(result.orders, [{
    currency: 'USD', order_id: '479150329953', status: 'paid', final: false, sold_at: '2026-10-29T17:50:44.543Z',
    totals: { item_revenue: '8.00', shipping_credit: '0.00', refunds: '0.00', fees: '1.77', shipping_cost: '0.00' },
    lines: [{ line_id: '352688723', listing_id: 'shop-sports-bra-90de', alt_ids: ['952073473'], weight: '8.00', item_revenue: '8.00' }],
  }]);
  assert.equal(JSON.stringify(result).includes('Buyer'), false);
  assert.equal(calls.some(call => call.url.includes('receipts/111/')), false);
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token');
});

test('Depop marks a refund that left the seller nothing as refunded, with the refund amount', async () => {
  const { c } = worker({
    'receipts/?role=seller': [200, { objects: [{ purchase_id: 5 }], page_info: { has_more: false } }],
    'receipts/5/': [200, { ...depopReceipt, purchase_id: 5, status: 'refunded', refund_details: { refund_amount: '8.66' },
      payout: { status: 'PAID', amount: '-4.76' } }],
  }, { access_token: 'token' });
  const [order] = (await read(c, 'depop')).orders;
  assert.deepEqual([order.status, order.final, order.totals.refunds], ['refunded', true, '8.66']);
});

test('Depop reads older unsettled orders beyond a page of settled orders', async () => {
  const { c, calls } = worker({
    'receipts/?role=seller': key => key.includes('after=next')
      ? [200, { objects: [{ purchase_id: 479150329953 }], page_info: { has_more: false } }]
      : [200, { objects: [{ purchase_id: 1 }], page_info: { has_more: true, last: 'next' } }],
    'receipts/479150329953/': [200, depopReceipt],
  }, { access_token: 'token' });
  assert.equal((await read(c, 'depop', ['1'])).orders.length, 1);
  assert.equal(calls.filter(call => call.url.includes('role=seller')).length, 2);
  assert.equal(calls.some(call => call.url.includes('receipts/1/')), false);
});

function rememberingStorage(c, initial = {}) {
  const store = { ...initial };
  c.chrome.storage.local.get = async key => (Object.hasOwn(store, key) ? { [key]: store[key] } : {});
  c.chrome.storage.local.set = async values => { Object.assign(store, JSON.parse(JSON.stringify(values))); };
  return store;
}

function depopTwoPages() {
  return worker({
    'receipts/?role=seller': key => key.includes('after=next')
      ? [200, { objects: [{ purchase_id: 479150329953 }], page_info: { has_more: false } }]
      : [200, { objects: [{ purchase_id: 1 }], page_info: { has_more: true, last: 'next' } }],
    'receipts/479150329953/': [200, depopReceipt],
  }, { access_token: 'token' });
}

test('between full walks, Depop stops at the first page of settled orders', async () => {
  const { c, calls } = depopTwoPages();
  rememberingStorage(c, { orderHistoryFullWalks: { depop: Date.now() - 60 * 1000 } });
  const result = await read(c, 'depop', ['1']);
  assert.deepEqual([result.ok, result.orders.length, result.more], [true, 0, false]);
  assert.equal(calls.filter(call => call.url.includes('role=seller')).length, 1);
});

test('a due full walk reads every page and records when it finished', async () => {
  const { c, calls } = depopTwoPages();
  const store = rememberingStorage(c, { orderHistoryFullWalks: { depop: Date.now() - 7 * 60 * 60 * 1000, poshmark: 5 } });
  const before = Date.now();
  assert.equal((await read(c, 'depop', ['1'])).orders.length, 1);
  assert.equal(calls.filter(call => call.url.includes('role=seller')).length, 2);
  assert.ok(store.orderHistoryFullWalks.depop >= before);
  assert.equal(store.orderHistoryFullWalks.poshmark, 5);
});

test('between full walks, a page with an unsettled order is still followed by the next page', async () => {
  const { c, calls } = depopTwoPages();
  rememberingStorage(c, { orderHistoryFullWalks: { depop: Date.now() } });
  await read(c, 'depop', []);
  assert.equal(calls.filter(call => call.url.includes('role=seller')).length, 2);
});

test('a pass that leaves orders unread makes the next pass a full walk', async () => {
  const { c } = worker({
    'receipts/?role=seller': [200, { objects: [{ purchase_id: 2 }], page_info: { has_more: true, last: 'next' } }],
    'receipts/2/': [200, { ...depopReceipt, purchase_id: 2 }],
  }, { access_token: 'token' });
  const store = rememberingStorage(c, { orderHistoryFullWalks: { depop: Date.now() } });
  // Every page holds an unsettled order, so the pass runs into the page limit.
  assert.equal((await read(c, 'depop', [])).more, true);
  assert.equal(Object.hasOwn(store.orderHistoryFullWalks, 'depop'), false);
});

test('Depop marks a page-limited history incomplete', async () => {
  const { c } = worker({
    'receipts/?role=seller': [200, { objects: [{ purchase_id: 1 }], page_info: { has_more: true, last: 'next' } }],
  }, { access_token: 'token' });
  assert.equal((await read(c, 'depop', ['1'])).more, true);
});

test('an unreadable bundle line prevents recording its full order total on the other lines', async () => {
  const { c } = worker({
    'receipts/?role=seller': [200, { objects: [{ purchase_id: 479150329953 }], page_info: { has_more: false } }],
    'receipts/479150329953/': [200, { ...depopReceipt, lines: [...depopReceipt.lines, { id: 2, slug: 'invalid slug' }] }],
  }, { access_token: 'token' });
  const result = await read(c, 'depop');
  assert.deepEqual(result.orders, []);
  assert.equal(result.unreadable, 1);
});

test('Depop without a sign-in reports signed out', async () => {
  const { c } = worker({});
  assert.deepEqual(await read(c, 'depop'), { ok: false, error: 'signed_out' });
});

function poshmarkJwt(userId) {
  const claims = Buffer.from(JSON.stringify({ user_id: userId, exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url');
  return `x.${claims}.y`;
}

function poshmarkPage(order) {
  return `<html><script>window.__INITIAL_STATE__ = ${JSON.stringify({ $_order_details: { order } })};</script></html>`;
}

test('Poshmark reads a bundle order page and a cancelled order', async () => {
  const seller = 'cccccccccccccccccccccccc';
  const bundle = 'aaaaaaaaaaaaaaaaaaaaaaaa';
  const cancelled = 'bbbbbbbbbbbbbbbbbbbbbbbb';
  const { c } = worker({
    [`/vm-rest/users/${seller}/orders/sales`]: [200, { data: { sales_summary: [
      { id: bundle, display_status: 'Order Complete', buyer: 'someone' },
      { id: cancelled, display_status: 'Order Cancelled' },
    ] }, meta: { next_max_id: '' } }],
    [`/order/sales/${bundle}`]: [200, poshmarkPage({ id: bundle, state: 'seller_confirm_initiated', inventory_booked_at: '2026-07-20T00:37:54-07:00',
      total_price_amount: { val: '28.0' }, pm_fee_amount: { val: '5.6' }, seller_shipping_discount_amount: null, buyer: 'someone',
      line_items: [{ id: 'l1', product_id: 'dddddddddddddddddddddddd', price_amount: { val: '12.0' } },
        { id: 'l2', product_id: 'eeeeeeeeeeeeeeeeeeeeeeee', price_amount: { val: '68.0' } }] })],
    [`/order/sales/${cancelled}`]: [200, poshmarkPage({ id: cancelled, state: 'cancelled', cancelled_on: '2026-09-18T00:00:00-07:00',
      inventory_booked_at: '2026-09-17T10:00:00-07:00', total_price_amount: { val: '18.0' }, pm_fee_amount: { val: '3.6' },
      line_items: [{ id: 'l3', product_id: 'ffffffffffffffffffffffff', price_amount: { val: '18.0' } }] })],
  }, { jwt: poshmarkJwt(seller) });
  const result = await read(c, 'poshmark');
  assert.equal(result.ok, true);
  const [first, second] = result.orders;
  assert.deepEqual(first, { currency: 'USD', order_id: bundle, status: 'paid', final: true, sold_at: '2026-07-20T07:37:54.000Z',
    totals: { item_revenue: '28.00', shipping_credit: '0.00', refunds: '0.00', fees: '5.60', shipping_cost: '0.00' },
    lines: [{ line_id: 'l1', listing_id: 'dddddddddddddddddddddddd', alt_ids: [], weight: '12.00', item_revenue: null },
      { line_id: 'l2', listing_id: 'eeeeeeeeeeeeeeeeeeeeeeee', alt_ids: [], weight: '68.00', item_revenue: null }] });
  assert.deepEqual([second.status, second.final, second.totals.item_revenue], ['cancelled', true, '0.00']);
  assert.equal(JSON.stringify(result).includes('someone'), false);
});

function mercariSummary(rows) {
  return { componentContents: [{ summaryContent: { soldTimeText: 'Sold on 10/11/26', orderItems: rows } }] };
}

function mercariRoutes(extra = {}) {
  return {
    '/v1/initialize': [200, { csrf: 'csrf', accessToken: 'token', user: { userId: 42, name: 'Seller' } }],
    ...extra,
  };
}

test('Mercari reads the seller side of each sold item and splits a bundle into its items', async () => {
  const { c } = worker(mercariRoutes({
    'userItemsQuery': key => [200, { data: { userItems: { pagination: { hasNext: false }, items: key.includes('sold_out') ? [
      { id: 'm42804244988', price: 2625, itemType: null, activeOrder: { orderId: 'o-1' } },
      { id: 'b46385261625', price: 3000, itemType: 'bundle', activeOrder: { orderId: 'o-2' } },
      { id: 'm41911006880', price: 1800, itemType: null, activeOrder: null },
    ] : [] } } }],
    'OrderStatusConsolidated': key => {
      const bundle = key.includes('"bundle"');
      const summary = mercariSummary(bundle ? [
        { title: 'Sold price', formattedPrice: '$30.00' }, { title: 'Selling fee', formattedPrice: '-$3.69' },
        { title: 'You made', formattedPrice: '$26.31', style: 'total' }] : [
        { title: 'Sold price', formattedPrice: '$26.25', style: 'unspecified' },
        { title: 'Selling fee', formattedPrice: '-$3.90', style: 'unspecified' },
        { title: 'You made', formattedPrice: '$22.35', style: 'total' },
        { title: 'Buyer also paid', formattedPrice: '', style: 'buyer_paid' },
        { title: 'Delivery', formattedPrice: '$12.83', style: 'unspecified' }]);
      if (bundle) summary.componentContents.push({ orderContent: { items: [{ id: 'm41911006880', price: 1800 }, { id: 'm81190248114', price: 1140 }] } });
      return [200, { data: { orderStatus: { contents: { orderId: bundle ? 'o-2' : 'o-1', orderState: 'order_complete', ...summary } } } }];
    },
  }));
  const result = await read(c, 'mercari');
  assert.equal(result.ok, true);
  assert.equal(result.orders.length, 2);
  const bundle = result.orders.find(order => order.order_id === 'o-2');
  assert.deepEqual(bundle.lines.map(line => [line.listing_id, line.weight]), [['m41911006880', '18.00'], ['m81190248114', '11.40']]);
  assert.equal(bundle.totals.item_revenue, '30.00');
  const single = result.orders.find(order => order.order_id === 'o-1');
  assert.deepEqual(single.totals, { item_revenue: '26.25', shipping_credit: '0.00', refunds: '0.00', fees: '3.90', shipping_cost: '0.00' });
  assert.equal(single.sold_at, '2026-10-11T12:00:00.000Z');
  assert.deepEqual(single.lines, [{ line_id: 'm42804244988', listing_id: 'm42804244988', alt_ids: [], weight: '26.25', item_revenue: null }]);
});

test('Mercari reports an order whose amounts do not add up as unreadable', async () => {
  const { c } = worker(mercariRoutes({
    'userItemsQuery': key => [200, { data: { userItems: { pagination: { hasNext: false }, items: key.includes('sold_out') ? [
      { id: 'm42804244988', price: 2625, itemType: null, activeOrder: { orderId: 'o-1' } }] : [] } } }],
    'OrderStatusConsolidated': [200, { data: { orderStatus: { contents: { orderId: 'o-1', orderState: 'order_complete', ...mercariSummary([
      { title: 'Sold price', formattedPrice: '$26.25' }, { title: 'Selling fee', formattedPrice: '-$3.90' },
      { title: 'You made', formattedPrice: '$20.00', style: 'total' }]) } } } }],
  }));
  const result = await read(c, 'mercari');
  assert.deepEqual([result.orders.length, result.unreadable], [0, 1]);
});

test('between full walks, Poshmark and Mercari stop at the first page of settled orders', async () => {
  const seller = 'cccccccccccccccccccccccc';
  const posh = worker({
    [`/vm-rest/users/${seller}/orders/sales`]: [200, { data: { sales_summary: [{ id: 'aaaaaaaaaaaaaaaaaaaaaaaa' }] },
      meta: { next_max_id: 'older' } }],
  }, { jwt: poshmarkJwt(seller) });
  rememberingStorage(posh.c, { orderHistoryFullWalks: { poshmark: Date.now() } });
  assert.equal((await read(posh.c, 'poshmark', ['aaaaaaaaaaaaaaaaaaaaaaaa'])).more, false);
  assert.equal(posh.calls.filter(call => call.url.includes('/orders/sales')).length, 1);

  const pages = [];
  const merc = worker(mercariRoutes({
    'userItemsQuery': key => {
      pages.push(key);
      return [200, { data: { userItems: { pagination: { hasNext: true },
        items: [{ id: 'm42804244988', price: 2625, itemType: null, activeOrder: { orderId: 'o-1' } }] } } }];
    },
  }));
  rememberingStorage(merc.c, { orderHistoryFullWalks: { mercari: Date.now() } });
  assert.equal((await read(merc.c, 'mercari', ['o-1'])).more, false);
  // One page each for orders in progress and sold items.
  assert.equal(pages.length, 2);
});
