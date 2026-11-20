const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, reply } = require('./worker-context');

function jwt(claims) {
  const payload = btoa(JSON.stringify(claims)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `e30.${payload}.sig`;
}

function worker({ cookies = {}, local = {}, session = null, fetch } = {}) {
  const c = loadWorker();
  Object.assign(c, { atob, AbortSignal });
  const stored = { ...local };
  c.chrome.storage = {
    local: {
      get: async key => ({ [key]: stored[key] }),
      set: async values => Object.assign(stored, values),
    },
    session: { get: async () => (session ? { mercari_session: session } : {}) },
  };
  c.chrome.cookies = {
    get: async query => cookies[`${query.url} ${query.name}`] || null,
    getAll: async query => cookies[query.url] || [],
  };
  if (fetch) c.fetch = fetch;
  return { c, stored };
}

test('a cookie session reports expiry and never the cookie value', async () => {
  const secret = 'depop-access-token-secret';
  const later = Math.floor(Date.now() / 1000) + 60 * 60;
  const { c } = worker({
    cookies: { 'https://www.depop.com/ access_token': { value: encodeURIComponent(secret), expirationDate: later } },
  });
  const summary = await c.readExtensionSession('depop');
  assert.equal(summary.state, 'signed_in');
  assert.equal(summary.expiring, true);
  assert.equal(summary.expires_at, later);
  assert.equal(JSON.stringify(summary).includes(secret), false);
});

test('an expired Poshmark token is expired even when the cookie date is later', async () => {
  const past = Math.floor(Date.now() / 1000) - 60;
  const later = Math.floor(Date.now() / 1000) + 86400;
  const token = jwt({ exp: past, username: 'ada' });
  const { c } = worker({
    cookies: { 'https://poshmark.com/ jwt': { value: token, expirationDate: later } },
  });
  const summary = await c.readExtensionSession('poshmark');
  assert.equal(summary.state, 'expired');
  assert.equal(summary.label, 'ada');
  assert.equal(summary.expires_at, past);
  assert.equal(JSON.stringify(summary).includes(token), false);
});

test('Mercari sign-in is read from its own initialize, never stored, and hides the token', async () => {
  const token = jwt({ exp: Math.floor(Date.now() / 1000) + 86400 * 10 });
  const { c, stored } = worker({ fetch: async url => (url === 'https://www.mercari.com/v1/initialize'
    ? reply(200, { csrf: 'csrf-secret', accessToken: token, user: { userId: 5, name: 'mercari-seller', email: 'secret@example.com' } })
    : reply(404, {})) });
  const summary = await c.readExtensionSession('mercari');
  assert.equal(summary.state, 'signed_in');
  assert.equal(summary.label, 'mercari-seller');
  for (const secret of [token, 'csrf-secret', 'secret@example.com']) assert.equal(JSON.stringify(summary).includes(secret), false);
  assert.equal(stored.mercari_session, undefined);
});

test('Mercari is signed out when initialize has no user, and unknown when it cannot be reached', async () => {
  const anonymous = worker({ fetch: async () => reply(200, { csrf: 'csrf', user: {} }) });
  assert.equal((await anonymous.c.readExtensionSession('mercari')).state, 'signed_out');
  const offline = worker({ fetch: async () => { throw new Error('offline'); } });
  assert.equal((await offline.c.readExtensionSession('mercari')).state, 'unknown');
});

// Settings → Connections on the web asks through the seller's connected Anteroom tab.
test('relayed sign-in checks report every marketplace without cookie values, and a sign-in opens only its page', async () => {
  const secret = 'depop-access-token-secret';
  const later = Math.floor(Date.now() / 1000) + 60 * 60 * 72;
  const { c } = worker({
    cookies: { 'https://www.depop.com/ access_token': { value: encodeURIComponent(secret), expirationDate: later } },
    fetch: async () => { throw new Error('offline'); },
  });
  const id = 'd'.repeat(32);
  const result = await c.runCloudMarketplaceCommand({ type: 'marketplace.sessions', job_id: null, message_id: id, payload: { request_id: id } }, []);
  assert.equal(result.ok, true);
  assert.deepEqual(Object.keys(result.sessions).sort(), ['depop', 'mercari', 'poshmark']);
  assert.equal(result.sessions.depop.state, 'signed_in');
  assert.equal(JSON.stringify(result).includes(secret), false);

  // A listing action is running: the sign-in page waits, and nothing opens.
  c.nativeOperationsRunning = () => true;
  c.chrome.tabs = { query: () => { throw new Error('read tabs'); }, create: () => { throw new Error('opened a tab'); } };
  const busy = await c.runCloudMarketplaceCommand({ type: 'marketplace.connect', job_id: null, message_id: id,
    payload: { request_id: id, marketplace: 'depop' } }, []);
  assert.deepEqual(JSON.parse(JSON.stringify(busy)), { ok: false, error: 'Wait for the current listing action to finish.' });
});

test('Depop reconnect verifies in the worker without waiting for page JavaScript', async () => {
  for (const [status, state] of [[200, 'signed_in'], [401, 'expired'], [403, 'expired'], [429, 'unknown'], [503, 'unknown']]) {
    const secret = 'fixture-depop-token';
    const { c } = worker({
      cookies: { 'https://www.depop.com/ access_token': { value: secret, expirationDate: Date.now() / 1000 + 86400 } },
      fetch: async (url, options) => {
        assert.equal(url, 'https://webapi.depop.com/presentation/api/v1/drafts/?limit=24&sort_by=createdAt');
        assert.equal(options.headers.Authorization, `Bearer ${secret}`);
        assert.equal(options.credentials, 'omit');
        assert.ok(options.signal instanceof AbortSignal);
        return new Response(null, { status });
      },
    });
    let contextReady = false;
    c.ensureMarketplaceRequestContext = async () => { contextReady = true; };
    c.chrome.tabs = { query: async () => [{ id: 7, url: 'https://www.depop.com/sellinghub/drafts/' }], update: async () => {} };
    c.nativeWait = async () => {};
    c.chrome.scripting = { executeScript: () => { throw new Error('Page JavaScript is unavailable'); } };
    const result = await c.runCloudMarketplaceCommand({ type: 'marketplace.connect', payload: { marketplace: 'depop' } }, []);
    assert.equal(result.ok, true);
    assert.equal(contextReady, true);
    assert.equal(result.state, state, String(status));
    assert.equal(result.signed_in, state === 'signed_in');
    assert.equal(JSON.stringify(result).includes(secret), false);
  }
});

test('Depop reconnect cannot report readiness when its verification read fails', async () => {
  let timeoutMs;
  const { c } = worker({
    cookies: { 'https://www.depop.com/ access_token': { value: 'fixture-token' } },
    fetch: async (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true });
    }),
  });
  c.AbortSignal = { timeout: ms => { timeoutMs = ms; return AbortSignal.timeout(10); } };
  c.chrome.tabs = { query: async () => [{ id: 7, url: 'https://www.depop.com/sellinghub/drafts/' }], update: async () => {} };
  c.nativeWait = async () => {};
  c.chrome.scripting = { executeScript: async () => [{ result: { page_read_status: 200 } }] };
  const keepAlive = setTimeout(() => {}, 1000);
  let result;
  try {
    result = await c.runCloudMarketplaceCommand({ type: 'marketplace.connect', payload: { marketplace: 'depop' } }, []);
  } finally { clearTimeout(keepAlive); }
  assert.equal(timeoutMs, 8000);
  assert.equal(result.ok, false);
  assert.match(result.error, /Depop.*sign-in check.*try again/i);
});

test('a stalled Depop request setup times out and a retry prepares it again', async () => {
  const { c } = worker({ fetch: async () => new Response(null, { status: 200 }) });
  let setups = 0;
  let finishSetup;
  c.chrome.declarativeNetRequest.updateSessionRules = () => {
    setups += 1;
    return setups === 1 ? new Promise(resolve => { finishSetup = resolve; }) : Promise.resolve();
  };
  c.depopBearer = async () => 'Bearer fixture-token';
  c.AbortSignal = { timeout: () => AbortSignal.timeout(10) };
  const keepAlive = setTimeout(() => {}, 1000);
  try {
    await assert.rejects(c.checkDepopSignIn(), { name: 'TimeoutError' });
    assert.equal((await c.checkDepopSignIn()).status, 200);
    assert.equal(setups, 2);
    finishSetup();
  } finally { clearTimeout(keepAlive); }
});
