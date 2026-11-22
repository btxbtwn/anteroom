const test = require('node:test');
const assert = require('node:assert/strict');
const { createCloudConnection, defaultUploadCloudSession } = require('../background/cloud-connection');

const ACCOUNT = '11111111-1111-4111-8111-111111111111';
const OTHER_ACCOUNT = '22222222-2222-4222-8222-222222222222';
const TOKEN = `${ACCOUNT}.abcdefghijklmnopqrstuvwxyz123456`;

function fakeChrome() {
  const session = {};
  const local = {};
  const windows = new Map();
  let nextWin = 100;
  return {
    runtime: {
      id: 'ext-id',
      getURL: file => `chrome-extension://ext-id/${file}`,
      getManifest: () => ({ version: '0.2.84' }),
    },
    storage: {
      session: {
        get: async key => ({ [key]: session[key] }),
        set: async obj => Object.assign(session, obj),
      },
      local: {
        get: async key => ({ [key]: local[key] }),
        set: async obj => Object.assign(local, obj),
      },
    },
    windows: {
      create: async opts => {
        const id = ++nextWin;
        windows.set(id, opts);
        return { id };
      },
      remove: async id => {
        windows.delete(id);
      },
    },
    tabs: {
      get: async id => ({ id, url: 'https://myanteroom.com/settings' }),
      sendMessage: async () => ({ current: true }),
    },
    _windows: windows,
  };
}

const pageSender = {
  id: 'ext-id',
  tab: { id: 7, url: 'https://myanteroom.com/settings' },
  frameId: 0,
  documentId: 'doc-1',
  url: 'https://myanteroom.com/settings',
  origin: 'https://myanteroom.com',
};

const popupSender = {
  id: 'ext-id',
  url: 'chrome-extension://ext-id/cloud-session-connect.html#req',
};

for (const marketplace of ['poshmark', 'mercari', 'depop']) {
test(`cloud_session_connect opens trusted ${marketplace} popup and uploads directly on approve`, async () => {
  const api = fakeChrome();
  const uploads = [];
  let notify;
  const started = new Promise(resolve => { notify = resolve; });
  let finish;
  const release = new Promise(resolve => { finish = resolve; });
  const bridge = createCloudConnection(api, {
    randomId: () => '33333333-3333-4333-8333-333333333333',
    uploadCloudSession: async opts => {
      uploads.push(opts);
      notify();
      await release;
      return { ok: true };
    },
  });

  const start = await bridge.handle({
    type: 'cloud.connection',
    action: 'cloud_session_connect',
    accountId: ACCOUNT,
    email: 'seller@example.com',
    marketplace,
    uploadToken: TOKEN,
  }, pageSender);
  assert.deepEqual(start, { ok: true, state: 'pending' });
  assert.equal(api._windows.size, 1);

  const details = await bridge.handle({
    type: 'cloud.connection',
    action: 'cloud_session_details',
    requestId: '33333333-3333-4333-8333-333333333333',
  }, popupSender);
  assert.deepEqual(details, { ok: true, email: 'seller@example.com', marketplace });

  const approved = bridge.handle({
    type: 'cloud.connection',
    action: 'cloud_session_approve',
    requestId: '33333333-3333-4333-8333-333333333333',
  }, popupSender);
  await started;
  assert.deepEqual(uploads, [{ marketplace, uploadToken: TOKEN }]);

  // Replaying the same requestId is rejected because it was consumed immediately.
  const replay = await bridge.handle({
    type: 'cloud.connection',
    action: 'cloud_session_approve',
    requestId: '33333333-3333-4333-8333-333333333333',
  }, popupSender);
  assert.deepEqual(replay, { ok: false });
  finish();
  assert.deepEqual(await approved, { ok: true });
});
}

test('cloud_session_connect rejects mismatched account/token prefix and unsupported marketplaces', async () => {
  const api = fakeChrome();
  const bridge = createCloudConnection(api);

  const badAccount = await bridge.handle({
    type: 'cloud.connection',
    action: 'cloud_session_connect',
    accountId: OTHER_ACCOUNT,
    email: 'seller@example.com',
    marketplace: 'poshmark',
    uploadToken: TOKEN,
  }, pageSender);
  assert.equal(badAccount.ok, false);

  const badMarket = await bridge.handle({
    type: 'cloud.connection',
    action: 'cloud_session_connect',
    accountId: ACCOUNT,
    email: 'seller@example.com',
    marketplace: 'ebay',
    uploadToken: TOKEN,
  }, pageSender);
  assert.equal(badMarket.ok, false);
});

test('defaultUploadCloudSession strips non-poshmark cookies and sends X-Cloud-Upload-Token without credentials', async () => {
  const calls = [];
  const originalFetch = global.fetch;
  global.fetch = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    };
  };
  try {
    const result = await defaultUploadCloudSession({
      cookies: {
        getAll: async () => [
          { name: 'jwt', value: 'header.payload.sig', domain: '.poshmark.com', path: '/', secure: true, httpOnly: true },
          { name: '_ps_session', value: 'abc', domain: 'poshmark.com', path: '/' },
          { name: 'evil', value: 'nope', domain: 'evilposhmark.com', path: '/' },
        ],
      },
    }, { marketplace: 'poshmark', uploadToken: TOKEN });
    assert.deepEqual(result, { ok: true });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'https://myanteroom.com/api/cloud-sessions/poshmark/upload');
    assert.equal(calls[0].init.credentials, 'omit');
    assert.equal(calls[0].init.headers['X-Cloud-Upload-Token'], TOKEN);
    const body = JSON.parse(calls[0].init.body);
    assert.deepEqual(body.cookies.map(c => c.name), ['jwt', '_ps_session']);
  } finally {
    global.fetch = originalFetch;
  }
});
