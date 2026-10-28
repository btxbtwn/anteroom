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

test('Sellwild uses the session response and hides the account payload', async () => {
  const { c } = worker({
    cookies: { 'https://sellwild.com/': [{ name: 'authjs.session-token', expirationDate: Math.floor(Date.now() / 1000) + 3600 }] },
    fetch: async () => reply(200, { user: { username: 'wild', email: 'secret@example.com', id: 'user-secret' } }),
  });
  const summary = await c.readExtensionSession('sellwild');
  assert.equal(summary.state, 'signed_in');
  assert.equal(summary.label, 'wild');
  assert.equal(summary.expiring, true);
  const text = JSON.stringify(summary);
  assert.equal(text.includes('secret@example.com'), false);
  assert.equal(text.includes('user-secret'), false);
});

test('Vinted sign-in is the current user login, and the week it lasts, never the hour', async () => {
  const token = jwt({ exp: Math.floor(Date.now() / 1000) + 3600, sub: '42' });
  const week = Math.floor(Date.now() / 1000) + 86400 * 7;
  const { c } = worker({
    cookies: {
      'https://www.vinted.com/ access_token_web': { value: token, expirationDate: week },
      'https://www.vinted.com/ refresh_token_web': { value: jwt({ exp: week }), expirationDate: week },
    },
    fetch: async url => (url === 'https://www.vinted.com/api/v2/users/current'
      ? reply(200, { user: { id: 42, login: 'ada', email: 'secret@example.com', real_name: 'Ada Lovelace' } })
      : reply(404, {})),
  });
  const summary = await c.readExtensionSession('vinted');
  assert.equal(summary.state, 'signed_in');
  assert.equal(summary.label, 'ada');
  // The access token lasts an hour; the hour is renewed, so that is not the sign-in.
  assert.equal(summary.expires_at, week);
  assert.equal(summary.expiring, false);
  const text = JSON.stringify(summary);
  assert.equal(text.includes(token), false);
  assert.equal(text.includes('secret@example.com'), false);
  assert.equal(text.includes('Ada Lovelace'), false);
});

test('a Vinted hour that ran out is renewed instead of reporting a sign-out', async () => {
  const week = Math.floor(Date.now() / 1000) + 86400 * 7;
  const calls = [];
  const { c } = worker({
    cookies: {
      'https://www.vinted.com/ access_token_web': { value: jwt({ exp: Math.floor(Date.now() / 1000) - 60 }), expirationDate: week },
      'https://www.vinted.com/ refresh_token_web': { value: jwt({ exp: week }), expirationDate: week },
    },
    fetch: async (url, options = {}) => {
      calls.push(`${options.method || 'GET'} ${url}`);
      if (url === 'https://www.vinted.com/web/api/auth/refresh') return reply(200, { access_token: jwt({ exp: Math.floor(Date.now() / 1000) + 3600 }) });
      if (url === 'https://www.vinted.com/api/v2/users/current') return reply(200, { user: { id: 42, login: 'ada' } });
      return reply(404, {});
    },
  });
  const summary = await c.readExtensionSession('vinted');
  assert.equal(summary.state, 'signed_in');
  assert.equal(summary.label, 'ada');
  assert.deepEqual(calls, ['POST https://www.vinted.com/web/api/auth/refresh', 'GET https://www.vinted.com/api/v2/users/current']);
});

test('an anonymous Vinted token is signed out, and a failed check is unknown', async () => {
  const anonymous = worker({
    fetch: async () => reply(200, { user: { id: null, login: null, anon_id: 'anon-secret', email: 'secret@example.com' } }),
  });
  const signedOut = await anonymous.c.readExtensionSession('vinted');
  assert.equal(signedOut.state, 'signed_out');
  assert.equal(JSON.stringify(signedOut).includes('anon-secret'), false);
  const rejected = worker({
    fetch: async (url, options = {}) => ((options.method || 'GET') === 'POST'
      ? reply(401, { message_code: 'invalid_authentication_token' })
      : reply(401, { message_code: 'invalid_authentication_token', access_token_web: 'secret-token' })),
  });
  assert.equal((await rejected.c.readExtensionSession('vinted')).state, 'signed_out');
  const offline = worker({ fetch: async () => { throw new Error('offline'); } });
  assert.deepEqual(JSON.parse(JSON.stringify(await offline.c.readExtensionSession('vinted'))), { state: 'unknown', expires_at: null, label: null, expiring: false });
});

test('Sellwild is unknown when the check cannot reach the site', async () => {
  const { c } = worker({ fetch: async () => { throw new Error('offline'); } });
  assert.deepEqual(JSON.parse(JSON.stringify(await c.readExtensionSession('sellwild'))), { state: 'unknown', expires_at: null, label: null, expiring: false });
});
