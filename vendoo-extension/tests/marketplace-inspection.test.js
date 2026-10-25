const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

test('marketplace inspection omits values, inline state and script query strings', async () => {
  const c = loadWorker();
  c.performance = { getEntriesByType: () => [] };
  c.location = { origin: 'https://www.depop.com', href: 'https://www.depop.com/sellinghub/drafts/?private=query' };
  c.document = { title: 'Drafts', readyState: 'complete', scripts: [{ src: 'https://www.depop.com/_next/draft.js?private=query' }],
    querySelectorAll: () => [] };
  const result = await c.inspectMarketplaceDocument(c.location.origin, null);
  assert.equal(result.ok, true);
  assert.equal(result.url, 'https://www.depop.com/sellinghub/drafts/');
  assert.equal(result.scripts[0], 'https://www.depop.com/_next/draft.js');
  assert.ok(!JSON.stringify(result).includes('private'));
});

test('inspection cannot fetch unobserved, private or foreign scripts', async () => {
  const c = loadWorker();
  c.performance = { getEntriesByType: () => [] };
  c.location = { origin: 'https://www.depop.com', href: 'https://www.depop.com/sellinghub/drafts/' };
  c.document = { scripts: [{ src: 'https://127.0.0.1/private.js' }] };
  let fetched = false;
  c.fetch = async () => { fetched = true; };
  for (const url of ['https://www.depop.com/api/session', 'https://127.0.0.1/private.js', 'https://unrelated.example/script.js']) {
    assert.ok((await c.inspectMarketplaceDocument(c.location.origin, url)).error);
  }
  assert.equal(fetched, false);
});

test('an active automation job blocks page inspection', async () => {
  const c = loadWorker();
  let reply;
  c.sendBrowserResult = (_msg, result) => { reply = result; };
  require('node:vm').runInContext('activeJob = {job_id: "busy"}', c);
  await c.handleMarketplaceInspection({ payload: { marketplace: 'depop' } });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /running/);
});
