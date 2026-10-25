const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker } = require('./worker-context');

function jwt(claims) {
  const payload = btoa(JSON.stringify(claims)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `e30.${payload}.sig`;
}

function worker({ cookies = {}, fetch } = {}) {
  const c = loadWorker();
  Object.assign(c, { atob, AbortSignal });
  c.chrome.cookies = { get: async query => cookies[`${query.url} ${query.name}`] || null };
  if (fetch) c.fetch = fetch;
  return c;
}

test('Poshmark sales keep listing and order ids and drop the buyer', async () => {
  const secret = 'buyer-pat@example.com';
  const c = worker({
    cookies: { 'https://poshmark.com/ jwt': { value: jwt({ user_id: 'aaaaaaaaaaaaaaaaaaaaaaaa', username: 'ada' }) } },
    fetch: async url => {
      assert.match(String(url), /\/vm-rest\/users\/aaaaaaaaaaaaaaaaaaaaaaaa\/orders\/sales/);
      return { ok: true, status: 200, json: async () => ({ data: {
        seller: { id: 'aaaaaaaaaaaaaaaaaaaaaaaa', email: secret },
        sales: [{ id: 'bbbbbbbbbbbbbbbbbbbbbbbb', buyer: { username: secret },
          line_items: [{ post_id: 'cccccccccccccccccccccccc', price_amount: { val: '40.00' } }] }],
      } }) };
    },
  });
  const result = JSON.parse(JSON.stringify(await c.readMarketplaceSales('poshmark')));
  assert.deepEqual(result, { ok: true, sales: [{ listing_id: 'cccccccccccccccccccccccc', order_id: 'bbbbbbbbbbbbbbbbbbbbbbbb' }] });
  assert.equal(JSON.stringify(result).includes(secret), false);
  assert.equal(JSON.stringify(result).includes('40.00'), false);
});

test('Depop receipts keep product and purchase ids only', async () => {
  const c = worker({
    cookies: { 'https://www.depop.com/ access_token': { value: 'depop-secret-token' } },
    fetch: async url => {
      assert.equal(String(url), 'https://webapi.depop.com/api/v1/receipts/?role=seller&limit=24');
      return { ok: true, status: 200, json: async () => ({ objects: [{ purchase_id: 900, buyer: { username: 'pat' },
        products: [{ product_id: 12345678, price: '20.00' }] }] }) };
    },
  });
  const result = JSON.parse(JSON.stringify(await c.readMarketplaceSales('depop')));
  assert.deepEqual(result, { ok: true, sales: [{ listing_id: '12345678', order_id: '900' }] });
  assert.equal(JSON.stringify(result).includes('pat'), false);
  assert.equal(JSON.stringify(result).includes('depop-secret-token'), false);
});

test('Mercari and Sellwild report a known listing only when it is sold', async () => {
  const calls = [];
  const c = worker({ fetch: async url => {
    calls.push(String(url));
    if (String(url).includes('mercari')) {
      const sold = String(url).includes('m12345678901');
      const id = sold ? 'm12345678901' : 'm10987654321';
      return { ok: true, status: 200, text: async () => `<script>{"status":"sold","id":"m00000000000"}</script><script>{"id":"${id}","buyer":"Pat","status":"${sold ? 'sold' : 'on_sale'}","price":12}</script>` };
    }
    return { ok: true, status: 200, json: async () => ({ listing: { id: 42, status: 'sold', buyer: { name: 'Pat' }, price: 12 } }) };
  } });
  const mercari = JSON.parse(JSON.stringify(await c.readMarketplaceSales('mercari', ['m1', 'm12345678901', 'm10987654321'])));
  assert.deepEqual(mercari, { ok: true, sales: [{ listing_id: 'm12345678901', order_id: 'm12345678901' }] });
  assert.equal(JSON.stringify(mercari).includes('Pat'), false);
  const sellwild = JSON.parse(JSON.stringify(await c.readMarketplaceSales('sellwild', ['42', 'not-an-id'])));
  assert.deepEqual(sellwild, { ok: true, sales: [{ listing_id: '42', order_id: '42' }] });
  assert.equal(JSON.stringify(sellwild).includes('Pat'), false);
  assert.equal(calls.some(url => url.includes('/us/item/m1/')), false);
});

test('a sale check waits while another marketplace action is running', async () => {
  const c = worker({ fetch: async () => { throw new Error('should not read'); } });
  vm.runInContext('activeNativeOperation = { id: "busy" }', c);
  assert.deepEqual(JSON.parse(JSON.stringify(await c.readMarketplaceSales('poshmark'))), { ok: true, sales: [], skipped: true });
  vm.runInContext('activeNativeOperation = null', c);
});
