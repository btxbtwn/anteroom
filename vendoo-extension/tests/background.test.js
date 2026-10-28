const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call } = require('./worker-context');

const worker = loadWorker();
const realVendooFetch = worker.vendooFetch;

test('reconnect alarm is registered for MV3 wakeups', () => {
  call(worker, 'ensureReconnectAlarm');
  const alarms = worker.__alarms.filter((alarm) => alarm.name === 'studio-reconnect');
  assert.ok(alarms.length >= 1);
  assert.equal(alarms[0].periodInMinutes, 0.5);
  assert.ok(worker.__alarmListeners.length >= 1);
});

test('ensureStudioConnection resets backoff and opens a socket when idle', async () => {
  const vm = require('node:vm');
  vm.runInContext('reconnectAttempt = 4; paired = false; ws = null;', worker);
  const result = JSON.parse(JSON.stringify(await vm.runInContext(
    "ensureStudioConnection({ resetBackoff: true })",
    worker,
  )));
  assert.equal(vm.runInContext('reconnectAttempt', worker), 0);
  assert.equal(result.ok, true);
  assert.equal(result.connecting, true);
  assert.ok(vm.runInContext('ws', worker));
});

// A worker that pairs and drops straight away used to reconnect every 500ms
// forever, so only a socket that stays up clears the backoff.
function openAndCloseWorkerSocket(worker, livedMs) {
  const vm = require('node:vm');
  vm.runInContext('sendIdent = async () => {}; sendPendingObservations = async () => {};', worker);
  vm.runInContext('ws = null; paired = false; reconnectTimer = null; socketOpenedAt = 0;', worker);
  const realNow = vm.runInContext('Date.now', worker);
  worker.__realNow = realNow;
  vm.runInContext('Date.now = () => 1000;', worker);
  vm.runInContext('connect()', worker);
  vm.runInContext('ws.onopen();', worker);
  vm.runInContext(`Date.now = () => ${1000 + livedMs};`, worker);
  vm.runInContext('ws.onclose({ code: 1006 });', worker);
  vm.runInContext('Date.now = __realNow', worker);
  return vm.runInContext('reconnectAttempt', worker);
}

test('a socket that drops at once keeps the retry backoff', () => {
  const vm = require('node:vm');
  vm.runInContext('reconnectAttempt = 3;', worker);
  assert.equal(openAndCloseWorkerSocket(worker, 200), 4);
});

test('a socket that stays up clears the retry backoff', () => {
  const vm = require('node:vm');
  vm.runInContext('reconnectAttempt = 3;', worker);
  // Reset on close, then the retry it schedules counts as the first one.
  assert.equal(openAndCloseWorkerSocket(worker, 20000), 1);
});

test('Vendoo URL helpers recognise draft pages', () => {
  assert.equal(call(worker, 'isNewItemUrl', 'https://web.vendoo.co/app/item/new?marketplace=general'), true);
  assert.equal(call(worker, 'isNewItemUrl', 'https://web.vendoo.co/app/item/abc123'), false);
  assert.equal(call(worker, 'isVendooUrl', 'https://app.vendoo.co/app/item/abc'), true);
  assert.equal(call(worker, 'isVendooUrl', 'https://www.ebay.com/sl/sell'), false);
  assert.equal(call(worker, 'isVendooUrl', ''), false);
});

test('marketplace query is set and compared case-insensitively', () => {
  const url = call(worker, 'withMarketplaceQuery', 'https://web.vendoo.co/app/item/abc?marketplace=general', 'ETSY');
  assert.equal(url, 'https://web.vendoo.co/app/item/abc?marketplace=etsy');
  assert.equal(call(worker, 'listingUrlsMatch', 'https://web.vendoo.co/app/item/abc?marketplace=Etsy', url), true);
  assert.equal(call(worker, 'listingUrlsMatch', 'https://web.vendoo.co/app/item/abc?marketplace=ebay', url), false);
  assert.equal(call(worker, 'listingUrlsMatch', 'https://web.vendoo.co/app/item/xyz', 'https://web.vendoo.co/app/item/abc'), false);
});

test('only durable Vendoo item ids are extracted', () => {
  assert.equal(call(worker, 'extractItemIdFromUrl', 'https://web.vendoo.co/app/item/65f0c1?marketplace=ebay'), '65f0c1');
  assert.equal(call(worker, 'extractItemIdFromUrl', 'https://web.vendoo.co/app/item/new'), '');
  assert.equal(call(worker, 'durableItemId', ' Edit '), '');
  assert.equal(call(worker, 'extractItemIdFromUrl', 'not a url'), '');
});

test('fill fields are grouped per marketplace in batches of 25', () => {
  const fields = [
    ...Array.from({ length: 26 }, (_, i) => ({ marketplace: 'EBAY', field: `f${i}` })),
    { field: 'Title' },
  ];
  const groups = call(worker, 'groupFillFieldMarketplaces', fields);
  assert.deepEqual(groups.map((g) => [g.marketplace, g.batches.map((b) => b.length)]), [
    ['ebay', [25, 1]],
    ['general', [1]],
  ]);
  assert.deepEqual(call(worker, 'groupFillFieldMarketplaces', null), []);
});

test('fill logs merge entries across batches', () => {
  assert.equal(call(worker, 'mergeFillLogs', [{}, { fill_log: { entries: [] } }]), null);
  assert.deepEqual(
    call(worker, 'mergeFillLogs', [
      { fill_log: { entries: [{ marketplace: 'depop', field: 'Style' }] } },
      { fill_log: { entries: [{ marketplace: 'depop', field: 'Parcel size' }] } },
    ]),
    { marketplace: 'depop', entries: [{ marketplace: 'depop', field: 'Style' }, { marketplace: 'depop', field: 'Parcel size' }] },
  );
});

test('save commands target the right Vendoo tab', () => {
  assert.deepEqual(call(worker, 'saveCommandForMarketplace', 'Mercari'), { type: 'SAVE_MARKETPLACE', platform: 'mercari' });
  assert.deepEqual(call(worker, 'saveCommandForMarketplace', 'unknown'), { type: 'SAVE_GENERAL' });
  assert.deepEqual(call(worker, 'saveCommandForMarketplace', ''), { type: 'SAVE_GENERAL' });
});

test('command timeouts scale with work and stay bounded', () => {
  assert.equal(call(worker, 'commandTimeoutMs', { type: 'FILL_FIELDS', fields: [] }), 90000);
  assert.equal(call(worker, 'commandTimeoutMs', { type: 'FILL_FIELDS', fields: new Array(100).fill({}) }), 300000);
  assert.equal(call(worker, 'commandTimeoutMs', { type: 'UPLOAD_PHOTOS', files: new Array(8).fill({}) }), 100000);
  assert.equal(call(worker, 'commandTimeoutMs', { type: 'DISCOVER_SCHEMA', platforms: ['ebay'] }), 120000);
  assert.equal(call(worker, 'commandTimeoutMs', { type: 'SOMETHING_NEW' }), 45000);
});

test('step names map to marketplaces', () => {
  assert.equal(call(worker, 'marketplaceFromStep', 'filling_etsy'), 'etsy');
  assert.equal(call(worker, 'marketplaceFromStep', 'auditing_marketplaces'), 'general');
  assert.equal(call(worker, 'marketplaceFromStep', 'uploading_photos'), 'general');
});

test('resumed jobs keep preflight steps and skip ahead', () => {
  const steps = ['opening_vendoo', 'waiting_ready', 'uploading_photos', 'filling_general', 'filling_ebay', 'saving_ebay']
    .map((step) => ({ step }));
  const resumed = call(worker, 'selectJobSteps', { options: { resumeFrom: 'filling_ebay' } }, steps);
  assert.deepEqual(resumed.map((s) => s.step), ['opening_vendoo', 'waiting_ready', 'filling_ebay', 'saving_ebay']);
  const unknown = call(worker, 'selectJobSteps', { options: { resumeFrom: 'filling_vinted' } }, steps);
  assert.equal(unknown.length, steps.length);
});

test('Vendoo item payloads are compacted before crossing the websocket', () => {
  const compact = call(worker, 'compactVendooValue', {
    title: 'x'.repeat(9000),
    photo: `data:image/png;base64,${'A'.repeat(300)}`,
    images: Array.from({ length: 50 }, (_, i) => i),
    generalDetails: { brand: "Levi's" },
  }, 0);
  assert.deepEqual(Object.keys(compact).slice(0, 2), ['generalDetails', 'images']);
  assert.equal(compact.images.length, 40);
  assert.match(compact.title, /…\[truncated 9000 chars\]$/);
  assert.equal(compact.photo, '[data-url 322 chars]');
});

test('imported listings keep unique image URLs only', () => {
  const urls = call(worker, 'collectImportedImageUrls',
    { images: ['//res.cloudinary.com/demo/a.jpg', 'https://example.com/readme.txt', 'data:image/png;base64,AAAA'] },
    [{ url: 'https://res.cloudinary.com/demo/a.jpg' }, 'https://cdn.example.com/b.webp?w=800'],
  );
  assert.deepEqual(urls, ['https://res.cloudinary.com/demo/a.jpg', 'https://cdn.example.com/b.webp?w=800']);
});

test('Vendoo label names resolve to ids, creating missing labels', async () => {
  const vm = require('node:vm');
  const requests = [];
  worker.__fetch = async (url, opts = {}) => {
    requests.push({ url, method: opts.method || 'GET', json: opts.json });
    if ((opts.method || 'GET') === 'GET') {
      return { ok: true, data: { documents: [
        { name: 'projects/p/databases/(default)/documents/users/u1/labels/lblToList', fields: { name: { stringValue: 'To List ' } } },
      ] } };
    }
    return { ok: true, data: {} };
  };
  vm.runInContext('vendooFetch = (...a) => __fetch(...a)', worker);
  const out = JSON.parse(JSON.stringify(await vm.runInContext(
    'resolveVendooLabels({ uid: "u1", access_token: "t" }, { names: ["to list", "Bin 4", "lblToList", "bin 4"] })',
    worker,
  )));

  assert.equal(out.ids.length, 2);
  assert.equal(out.ids[0], 'lblToList');
  assert.deepEqual(out.created, ['Bin 4']);
  const created = requests.find((r) => r.method === 'PATCH');
  assert.match(created.url, new RegExp(`/users/u1/labels/${out.ids[1]}$`));
  assert.equal(created.json.fields.name.stringValue, 'Bin 4');
  assert.equal(created.json.fields.id.stringValue, out.ids[1]);
});

test('list_labels returns id and name for each seller label', async () => {
  const vm = require('node:vm');
  worker.__fetch = async () => ({
    ok: true,
    data: {
      documents: [
        { name: 'projects/p/databases/(default)/documents/users/u1/labels/g8MHWF7KiscANFLZRGyM', fields: { name: { stringValue: 'Women' } } },
        { name: 'projects/p/databases/(default)/documents/users/u1/labels/0kGVwda9cRk55wmfd3Bq', fields: { name: { stringValue: 'To List' } } },
      ],
    },
  });
  vm.runInContext('vendooFetch = (...a) => __fetch(...a)', worker);
  const labels = JSON.parse(JSON.stringify(await vm.runInContext(
    'listVendooLabels({ uid: "u1", access_token: "t" })',
    worker,
  )));
  assert.deepEqual(labels, [
    { id: 'g8MHWF7KiscANFLZRGyM', name: 'Women' },
    { id: '0kGVwda9cRk55wmfd3Bq', name: 'To List' },
  ]);
});

test('Firestore documents decode back to plain Vendoo items', () => {
  const doc = {
    name: 'projects/p/databases/(default)/documents/users/u1/items/itm123',
    fields: {
      dateLastModified: { integerValue: '1750000000000' },
      generalDetails: {
        mapValue: {
          fields: {
            title: { stringValue: 'Nike Air Tee' },
            price: { doubleValue: 24.5 },
            tags: { arrayValue: { values: [{ stringValue: 'nike' }, { stringValue: 'tee' }] } },
            notes: { nullValue: null },
          },
        },
      },
      listings: {
        mapValue: {
          fields: {
            ebay: { mapValue: { fields: { status: { mapValue: { fields: { sold: { booleanValue: true } } } } } } },
          },
        },
      },
    },
  };

  assert.deepEqual(call(worker, 'firestoreItem', doc), {
    dateLastModified: 1750000000000,
    generalDetails: { title: 'Nike Air Tee', price: 24.5, tags: ['nike', 'tee'], notes: null },
    listings: { ebay: { status: { sold: true } } },
    id: 'itm123',
    itemID: 'itm123',
  });
  assert.equal(call(worker, 'firestoreItem', { fields: {} }), null);
});

test('Firebase timestamp objects encode as Firestore timestamps for form saves', () => {
  assert.deepEqual(
    call(worker, 'firestoreValue', { _seconds: 1750000000, _nanoseconds: 123456789 }),
    { timestampValue: '2025-06-15T15:06:40.123456789Z' },
  );
  assert.deepEqual(
    call(worker, 'firestoreValue', { _seconds: 1750000000, _nanoseconds: 0 }),
    { timestampValue: '2025-06-15T15:06:40.000000000Z' },
  );
  assert.deepEqual(
    call(worker, 'firestoreValue', { _seconds: 'not-a-timestamp', name: 'ordinary map' }),
    {
      mapValue: {
        fields: {
          _seconds: { stringValue: 'not-a-timestamp' },
          name: { stringValue: 'ordinary map' },
        },
      },
    },
  );
});

test('the inventory listing pages through Firestore and can ask for ids only', async () => {
  const vm = require('node:vm');
  const requests = [];
  worker.__fetch = async (url) => {
    requests.push(url);
    return {
      ok: true,
      data: {
        documents: [{ name: 'users/u1/items/itm1', fields: { id: { stringValue: 'itm1' } } }],
        nextPageToken: 'tok2',
      },
    };
  };
  vm.runInContext('vendooFetch = (...a) => __fetch(...a)', worker);

  const page = JSON.parse(JSON.stringify(await vm.runInContext(
    'listVendooItems({ uid: "u1", access_token: "t" }, { page_size: 50, page_token: "tok1" })',
    worker,
  )));
  assert.deepEqual(page.items, [{ id: 'itm1', itemID: 'itm1' }]);
  assert.equal(page.next_page_token, 'tok2');
  assert.match(requests[0], /\/users\/u1\/items\?/);
  assert.match(requests[0], /pageSize=50/);
  assert.match(requests[0], /pageToken=tok1/);
  assert.doesNotMatch(requests[0], /mask/);

  await vm.runInContext(
    'listVendooItems({ uid: "u1", access_token: "t" }, { page_size: 5000, ids_only: true })',
    worker,
  );
  assert.match(requests[1], /pageSize=300/);
  assert.match(requests[1], /mask.fieldPaths=id/);
  assert.doesNotMatch(requests[1], /pageToken/);
});

test('vendooFetch records the full request and masks credentials', async () => {
  const vm = require('node:vm');
  worker.AbortController = globalThis.AbortController;
  const previousFetch = worker.fetch;
  const jwt = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1MSJ9.c2lnbmF0dXJl';
  worker.fetch = async () => ({
    ok: true,
    status: 201,
    headers: new Map([['content-type', 'application/json'], ['x-request-id', 'r-42']]),
    text: async () => JSON.stringify({ id: 'abc', idToken: jwt, url: 'https://storage.googleapis.com/b/o?X-Goog-Signature=deadbeefcafe' }),
  });
  vm.runInContext('vendooApiLogQueue.length = 0; paired = false;', worker);
  try {
    const result = await realVendooFetch(
      'https://api.web.vendoo.co/api/item/abc?userId=u1&key=AIzaSECRETKEY',
      { method: 'POST', token: 'super-secret-token', json: { title: 'Levi 501', refresh_token: 'REFRESHSECRET' } },
    );
    const logs = JSON.parse(JSON.stringify(vm.runInContext('vendooApiLogQueue', worker)));
    assert.equal(result.status, 201);
    assert.equal(logs.length, 1);
    const [entry] = logs;
    assert.equal(entry.method, 'POST');
    assert.match(entry.url, /^https:\/\/api\.web\.vendoo\.co\/api\/item\/abc\?userId=u1&key=AIza…\(masked\)$/);
    assert.equal(entry.request_headers.Authorization, 'Bearer …(masked)');
    assert.equal(entry.request_headers['Content-Type'], 'application/json');
    assert.match(entry.request_body, /"title":"Levi 501"/);
    assert.equal(entry.response_headers['x-request-id'], 'r-42');
    assert.match(entry.response_body, /"id":"abc"/);
    const blob = JSON.stringify(entry);
    for (const secret of ['super-secret-token', 'AIzaSECRETKEY', 'REFRESHSECRET', jwt, 'deadbeefcafe']) {
      assert.equal(blob.includes(secret), false, secret);
    }
  } finally {
    worker.fetch = previousFetch;
    vm.runInContext('vendooApiLogQueue.length = 0;', worker);
  }
});

test('api log bodies describe binary uploads and cap long text', () => {
  const upload = worker.vendooApiLogEntry({
    method: 'PUT',
    url: 'https://storage.googleapis.com/bucket/object',
    status: 200,
    ok: true,
    requestBody: new Blob(['x'.repeat(2048)], { type: 'image/jpeg' }),
  });
  assert.equal(upload.request_body, '[binary image/jpeg, 2,048 bytes]');
  const long = worker.vendooApiLogEntry({ url: 'https://api.web.vendoo.co/x', ok: true, status: 200, responseBody: 'a'.repeat(70000) });
  assert.match(long.response_body, /truncated \(70,000 characters total\)$/);
  const failed = worker.vendooApiLogEntry({ url: 'https://api.web.vendoo.co/x', ok: false, status: null, error: 'Failed to fetch' });
  assert.equal(failed.error, 'Failed to fetch');
});

test('api log lines wait until Studio is paired', () => {
  const vm = require('node:vm');
  const sent = [];
  const previousSend = worker.send;
  worker.send = (message) => { sent.push(message); };
  const previousWs = worker.ws;
  const previousPaired = worker.paired;
  try {
    vm.runInContext('vendooApiLogQueue.length = 0; paired = false; ws = { readyState: 1 };', worker);
    for (let i = 0; i < 25; i += 1) {
      worker.recordVendooApiLog(worker.vendooApiLogEntry({
        method: 'POST',
        url: `https://securetoken.googleapis.com/v1/token?key=SECRETKEY${i}`,
        status: 200,
        ok: true,
        durationMs: 15,
      }));
    }
    assert.equal(sent.length, 0);
    assert.equal(vm.runInContext('JSON.stringify(vendooApiLogQueue).includes("SECRETKEY")', worker), false);
    vm.runInContext('paired = true;', worker);
    worker.flushVendooApiLogs();
    assert.equal(sent.length, 2);
    assert.equal(sent[0].type, 'vendoo.api_log');
    assert.equal(sent[0].payload.entries.length, 20);
    assert.equal(sent[1].payload.entries.length, 5);
    assert.match(sent[0].payload.entries[0].url, /^https:\/\/securetoken\.googleapis\.com\/v1\/token\?key=SECR…\(masked\)$/);
  } finally {
    worker.send = previousSend;
    worker.ws = previousWs;
    worker.paired = previousPaired;
    vm.runInContext('vendooApiLogQueue.length = 0;', worker);
  }
});

test('eBay sold search asks for sold, pre-owned, US listings', () => {
  const url = new URL(call(worker, 'ebaySoldSearchUrl', 'y2k jeans'));
  assert.equal(url.origin + url.pathname, 'https://www.ebay.com/sch/i.html');
  assert.deepEqual(Object.fromEntries(url.searchParams), {
    _nkw: 'y2k jeans', LH_Sold: '1', LH_Complete: '1', LH_ItemCondition: '3000', LH_PrefLoc: '1', _ipg: '60', rt: 'nc',
  });
});

test('eBay sold listings keep what loaded when a later search is blocked', async () => {
  const vm = require('node:vm');
  const result = JSON.parse(JSON.stringify(await vm.runInContext(`(async () => {
    const pages = [{ loaded: true, cards: [{ id: '1' }] }, { loaded: false, cards: [] }];
    const removed = [];
    let listener = null;
    const before = { tabs: chrome.tabs, scripting: chrome.scripting };
    chrome.tabs = {
      onUpdated: { addListener: (fn) => { listener = fn; }, removeListener: () => {} },
      create: async () => ({ id: 7 }),
      update: async (id, { url }) => { listener(id, { status: 'complete' }, { url }); },
      remove: async (id) => { removed.push(id); },
    };
    chrome.scripting = { executeScript: async () => [{ result: pages.shift() }] };
    const setTimeoutBefore = setTimeout;
    // The worker context's timers never fire; let the pause between searches pass.
    setTimeout = (fn, ms) => { if (ms < 5000) Promise.resolve().then(fn); return 0; };
    try {
      return { ...(await readEbaySoldListings(['y2k jeans', 'y2k dress', 'never reached'])), removed };
    } finally {
      setTimeout = setTimeoutBefore;
      Object.assign(chrome, before);
    }
  })()`, worker)));
  assert.deepEqual(result, {
    ok: false, results: { 'y2k jeans': [{ id: '1' }] }, error: 'eBay did not show search results', removed: [7],
  });
});

test('a refused duplicate copy stands down until the reconnect alarm', async () => {
  const vm = require('node:vm');
  const duplicate = { type: 'error', duplicate_bridge: true, message: 'Another copy of the Anteroom Bridge is loaded in Chrome.' };
  assert.equal(call(worker, 'isDuplicateCopyError', duplicate), true);
  assert.equal(call(worker, 'isDuplicateCopyError', { type: 'error', message: 'Another copy of the Anteroom Bridge is loaded in Chrome.' }), true);
  assert.equal(call(worker, 'isDuplicateCopyError', { type: 'error', message: 'Invalid pairing token' }), false);
  assert.equal(call(worker, 'isDuplicateCopyError', { type: 'connection.accepted' }), false);

  vm.runInContext('paired = true; duplicateBridge = false; ws = null;', worker);
  call(worker, 'standDownForDuplicateCopy');
  assert.equal(vm.runInContext('duplicateBridge', worker), true);
  assert.equal(vm.runInContext('paired', worker), false);

  // A quick retry here would restart the fight with the copy Studio is using.
  const quiet = JSON.parse(JSON.stringify(await vm.runInContext('ensureStudioConnection()', worker)));
  assert.equal(quiet.duplicateBridge, true);
  assert.equal(vm.runInContext('ws', worker), null);

  // The alarm is the one place that tries again.
  const retried = JSON.parse(JSON.stringify(await vm.runInContext('ensureStudioConnection({ fromAlarm: true })', worker)));
  assert.equal(retried.connecting, true);
  assert.equal(vm.runInContext('duplicateBridge', worker), false);
  assert.ok(vm.runInContext('ws', worker));
});
