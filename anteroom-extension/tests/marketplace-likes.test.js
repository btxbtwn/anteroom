const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

const USER = 'a'.repeat(24);
const POST = 'b'.repeat(24);
const AT = '2026-10-30T02:00:00Z';

function worker(body) {
  const c = loadWorker();
  c.poshmarkAccount = async () => ({ user_id: USER });
  c.depopBearer = async () => 'test-token';
  const requests = [];
  c.fetch = async (url, options) => {
    requests.push({ url: String(url), method: options.method || 'GET' });
    return reply(200, body);
  };
  return { c, requests };
}

test('Poshmark reads only like notifications and exports no buyer or message data', async () => {
  const { c, requests } = worker({ data: [
    { story_type: 'PersonFollow', content: { data: [{ news_item: { actor: 'private' } }] } },
    { story_type: 'PersonLikePost', content: { data: [{ news_item: {
      id: '123', created_at: AT, target: { data: { post_id: POST } }, actor: 'private', message: 'private',
    } }] } },
  ] });
  const result = JSON.parse(JSON.stringify(await c.readMarketplaceLikes({ marketplace: 'poshmark' })));
  assert.deepEqual(result, { ok: true, events: [{ listing_id: POST, at: '2026-10-30T02:00:00.000Z' }] });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, 'GET');
  assert.ok(requests[0].url.includes(`/users/${USER}/newsfeed/all?`));
});

test('Depop reads the signed-in shop feed and strips buyer details', async () => {
  const { c, requests } = worker({ objects: [{ product: { slug: 'seller-blue-coat', id: 123 },
    liked_at: AT, liker: { username: 'private' }, can_send_offer: true }], page_info: {} });
  const result = JSON.parse(JSON.stringify(await c.readMarketplaceLikes({ marketplace: 'depop' })));
  assert.deepEqual(result, { ok: true, events: [{ listing_id: 'seller-blue-coat', at: '2026-10-30T02:00:00.000Z' }] });
  assert.deepEqual(requests, [{ url: 'https://webapi.depop.com/presentation/api/v1/shops/me/products/likes/', method: 'GET' }]);
});

test('changed feeds fail closed and unsupported marketplaces never fetch', async () => {
  for (const body of [{}, { objects: [{ product: { slug: 'coat' }, liked_at: 'not a date' }] }]) {
    const { c } = worker(body);
    const result = await c.readMarketplaceLikes({ marketplace: 'depop' });
    assert.equal(result.ok, false);
    assert.equal(result.error_code, 'schema_drift');
  }
  const { c, requests } = worker({});
  assert.equal((await c.readMarketplaceLikes({ marketplace: 'mercari' })).ok, false);
  assert.equal(requests.length, 0);
});
