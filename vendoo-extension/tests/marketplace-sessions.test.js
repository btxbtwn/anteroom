const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

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

test('Mercari keeps a saved sign-in in local storage and drops an expired one', async () => {
  const token = jwt({ exp: Math.floor(Date.now() / 1000) + 86400 * 10, username: 'mercari-seller' });
  const { c } = worker({
    local: { mercari_session: { headers: { authorization: `Bearer ${token}`, 'x-csrf-token': 'csrf-secret' } } },
  });
  const summary = await c.readExtensionSession('mercari');
  assert.equal(summary.state, 'signed_in');
  assert.equal(summary.label, 'mercari-seller');
  assert.equal(JSON.stringify(summary).includes(token), false);
  assert.equal(JSON.stringify(summary).includes('csrf-secret'), false);

  const expired = jwt({ exp: Math.floor(Date.now() / 1000) - 5 });
  const stale = worker({
    local: { mercari_session: { headers: { authorization: `Bearer ${expired}`, 'x-csrf-token': 'csrf-secret' } } },
  });
  assert.equal((await stale.c.readExtensionSession('mercari')).state, 'expired');
  await assert.rejects(() => stale.c.mercariHeaders(), /Reconnect Mercari/);
});

test('a Mercari sign-in saved only for this browser session still counts', async () => {
  const { c } = worker({
    session: { headers: { authorization: 'Bearer opaque-session', 'x-csrf-token': 'csrf' } },
  });
  assert.equal((await c.readExtensionSession('mercari')).state, 'signed_in');
  const headers = await c.mercariHeaders();
  assert.equal(headers.authorization, 'Bearer opaque-session');
});

test('Sellwild uses the session response and hides the account payload', async () => {
  const { c } = worker({
    cookies: { 'https://sellwild.com/': [{ name: 'authjs.session-token', expirationDate: Math.floor(Date.now() / 1000) + 3600 }] },
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ user: { username: 'wild', email: 'secret@example.com', id: 'user-secret' } }) }),
  });
  const summary = await c.readExtensionSession('sellwild');
  assert.equal(summary.state, 'signed_in');
  assert.equal(summary.label, 'wild');
  assert.equal(summary.expiring, true);
  const text = JSON.stringify(summary);
  assert.equal(text.includes('secret@example.com'), false);
  assert.equal(text.includes('user-secret'), false);
});

test('Sellwild is unknown when the check cannot reach the site', async () => {
  const { c } = worker({ fetch: async () => { throw new Error('offline'); } });
  assert.deepEqual(JSON.parse(JSON.stringify(await c.readExtensionSession('sellwild'))), { state: 'unknown', expires_at: null, label: null, expiring: false });
});
