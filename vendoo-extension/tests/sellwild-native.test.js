const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker } = require('./worker-context');

function form() {
  const c = loadWorker();
  let clicks = 0;
  c.location = { origin: 'https://sellwild.com', pathname: '/sell' };
  c.getComputedStyle = () => ({ visibility: 'visible' });
  const visible = { getClientRects: () => [{}] };
  const fields = { title: 'Test', text: 'Private test', price: '12', category: 'Clothing', condition: 'Good' };
  c.document = {
    getElementById: id => ({ value: fields[id.replace('-input', '')] }),
    querySelectorAll: selector => selector === 'img' ? [{ ...visible, src: 'blob:photo1' }, { ...visible, src: 'blob:photo2' }]
      : [{ ...visible, innerText: 'Finish', click: () => { clicks++; } }],
  };
  return { c, fields, clicks: () => clicks, payload: { operation_id: 'abc123', action: 'publish', confirm: true,
    photo_count: 2, listing: { title: 'Test', description: 'Private test', price: 12 }, deadline: Date.now() + 30000 } };
}

test('Sellwild review cannot click Finish; publishing requires explicit approval', async () => {
  const s = form();
  for (const payload of [{ ...s.payload, action: 'review' }, { ...s.payload, confirm: false }]) {
    assert.ok((await s.c.sellwildPage('publish', payload)).error);
  }
  assert.equal(s.clicks(), 0);
  assert.equal((await s.c.sellwildPage('preflight', s.payload)).ok, true);
  assert.equal(s.clicks(), 0);
  assert.equal((await s.c.sellwildPage('publish', s.payload)).ok, true);
  assert.equal(s.clicks(), 1);
});

test('Sellwild rejects changed fields, missing category and mismatched photos before publishing', async () => {
  const s = form();
  s.fields.title = 'Changed';
  assert.ok((await s.c.sellwildPage('publish', s.payload)).error);
  s.fields.title = 'Test';
  s.fields.category = '';
  assert.ok((await s.c.sellwildPage('publish', s.payload)).error);
  s.fields.category = 'Clothing';
  assert.ok((await s.c.sellwildPage('publish', { ...s.payload, photo_count: 1 })).error);
  assert.equal(s.clicks(), 0);
});

test('Sellwild retains only matching create/activation receipts and distinguishes moderation', async () => {
  for (const underReview of [false, true]) {
    const c = loadWorker();
    const stored = {};
    let listener;
    let detach;
    c.chrome.storage.local.get = async key => ({ [key]: stored[key] });
    c.chrome.storage.local.set = async values => Object.assign(stored, values);
    c.chrome.debugger = {
      onEvent: { addListener: fn => { listener = fn; }, removeListener() {} },
      onDetach: { addListener: fn => { detach = fn; }, removeListener() {} },
      sendCommand: async (_target, _method, { requestId }) => ({ body: JSON.stringify(requestId === 'create'
        ? { success: true, listing: { id: 123, title: 'Test', ignored: 'never-store' } }
        : { success: true, underReview, ignored: 'never-store' }) }),
    };
    const observer = c.observeSellwildPublish(4, { listing: { title: 'Test', price: 12 } }, 'native_operation_abc123');
    const request = (id, url, body) => listener({ tabId: 4 }, 'Network.requestWillBeSent', {
      requestId: id, request: { method: 'POST', url, postData: JSON.stringify(body), headers: { Authorization: 'never-store' } },
    });
    request('create', 'https://sellwild.com/api/listings/create', { title: 'Test', price: 12 });
    listener({ tabId: 4 }, 'Network.loadingFinished', { requestId: 'create' });
    await new Promise(resolve => setImmediate(resolve));
    request('activate', 'https://sellwild.com/api/listings/123/confirm-photos', {});
    listener({ tabId: 4 }, 'Network.loadingFinished', { requestId: 'activate' });
    const result = await observer.result;
    assert.equal(result.state, underReview ? 'pending_review' : 'published');
    assert.equal(result.listing_url, 'https://sellwild.com/product/123/test');
    assert.equal(observer.started(), true);
    assert.ok(!JSON.stringify(stored).includes('never-store'));
    detach({ tabId: 4 });
    assert.equal(observer.connected(), false);
    await observer.close();
  }
});

test('an unexpected Sellwild create attempt cannot be classified as safe to retry', async () => {
  const c = loadWorker();
  let listener;
  c.chrome.debugger = { onEvent: { addListener: fn => { listener = fn; }, removeListener() {} },
    onDetach: { addListener() {}, removeListener() {} }, sendCommand: async () => ({}) };
  const observer = c.observeSellwildPublish(4, { listing: { title: 'Test', price: 12 } }, 'operation');
  listener({ tabId: 4 }, 'Network.requestWillBeSent', { requestId: 'wrong', request: {
    method: 'POST', url: 'https://sellwild.com/api/listings/create', postData: JSON.stringify({ title: 'Changed', price: 13 }),
  } });
  assert.equal(observer.started(), true);
  await observer.close();
});

test('Sellwild network guard permits one approved create and blocks duplicate or unrelated writes', async () => {
  const c = loadWorker();
  let listener;
  const commands = [];
  const stored = {};
  c.chrome.storage.local.get = async key => ({ [key]: stored[key] });
  c.chrome.storage.local.set = async values => Object.assign(stored, values);
  c.chrome.debugger = { onEvent: { addListener: fn => { listener = fn; }, removeListener() {} },
    onDetach: { addListener() {}, removeListener() {} },
    sendCommand: async (_target, method, args) => {
      if (method === 'Network.getResponseBody') return { body: JSON.stringify({ success: true, listing: { id: 123, title: 'Test' } }) };
      commands.push({ method, args }); return {};
    } };
  const observer = c.observeSellwildPublish(4, { action: 'publish', confirm: true, deadline: Date.now() + 30000,
    listing: { title: 'Test', price: 12 } }, 'operation');
  const paused = (requestId, path, body, method = 'POST') => listener({ tabId: 4 }, 'Fetch.requestPaused', {
    requestId, request: { method, url: 'https://sellwild.com'+path, postData: JSON.stringify(body) },
  });
  paused('wrong', '/api/listings/create', { title: 'Changed', price: 12 });
  paused('first', '/api/listings/create', { title: 'Test', price: 12 });
  paused('duplicate', '/api/listings/create', { title: 'Test', price: 12 });
  paused('edit', '/api/listings/999/edit', {}, 'PUT');
  paused('activate-wrong-item', '/api/listings/999/confirm-photos', {});
  assert.deepEqual(commands.map(command => command.method), ['Fetch.failRequest', 'Fetch.continueRequest', 'Fetch.failRequest', 'Fetch.failRequest', 'Fetch.failRequest']);
  listener({ tabId: 4 }, 'Network.requestWillBeSent', { requestId: 'create', request: {
    method: 'POST', url: 'https://sellwild.com/api/listings/create', postData: JSON.stringify({ title: 'Test', price: 12 }),
  } });
  listener({ tabId: 4 }, 'Network.loadingFinished', { requestId: 'create' });
  await new Promise(resolve => setImmediate(resolve));
  paused('activate', '/api/listings/123/confirm-photos', {});
  paused('activate-duplicate', '/api/listings/123/confirm-photos', {});
  assert.deepEqual(commands.slice(-2).map(command => command.method), ['Fetch.continueRequest', 'Fetch.failRequest']);
  assert.equal(observer.started(), true);
  await observer.close();
});

test('Sellwild publish commands are single-use and result checks never repeat the click', async () => {
  const c = loadWorker();
  const stored = {};
  const replies = [];
  let calls = 0;
  c.chrome.storage.local.get = async key => ({ [key]: stored[key] });
  c.chrome.storage.local.set = async values => Object.assign(stored, values);
  c.sendBrowserResult = (_msg, result) => replies.push(result);
  c.runSellwildNative = async () => { calls++; return { ok: true, state: 'published', listing_url: 'https://sellwild.com/product/123/test' }; };
  const msg = { type: 'marketplace.execute', job_id: 'abc123', payload: { operation_id: 'abc123', marketplace: 'sellwild', action: 'publish', confirm: true } };
  await c.handleNativeMarketplaceMessage(msg);
  await c.handleNativeMarketplaceMessage(msg);
  await c.handleNativeMarketplaceMessage({ ...msg, type: 'marketplace.result' });
  assert.equal(calls, 1);
  assert.equal(replies[2].result.state, 'published');
});
