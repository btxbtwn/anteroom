const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { loadWorker } = require('./worker-context');

test('content-script wakeups connect to Studio without a popup', async () => {
  const worker = loadWorker();
  const listener = worker.__messageListeners[0];
  const response = await new Promise((resolve) => {
    assert.equal(listener({ type: 'ENSURE_STUDIO_CONNECTION' }, {}, resolve), true);
  });
  assert.equal(response.ok, true);
  assert.equal(vm.runInContext('ws !== null', worker), true);
});

test('seller saves reach Studio while automation saves are ignored', () => {
  const worker = loadWorker();
  const messages = [];
  worker.send = (message) => messages.push(message);
  const listener = worker.__messageListeners[0];
  let response;
  const save = () => listener(
    { type: 'VENDOO_ITEM_SAVED', item_id: 'draft123' }, {}, (value) => { response = value; },
  );
  save();
  assert.equal(response.ok, true);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].type, 'vendoo.item_saved');
  assert.equal(messages[0].payload.item_id, 'draft123');

  vm.runInContext("activeJob = { job_id: 'job123' }", worker);
  save();
  assert.equal(response.skipped, 'active_job');
  assert.equal(messages.length, 1);
});

test('marketplace fill outcomes are acknowledged without popup broadcasts', () => {
  const worker = loadWorker();
  worker.chrome.runtime.sendMessage = () => assert.fail('Unexpected UI broadcast');
  for (const type of ['FILL_COMPLETE', 'FILL_ERROR']) {
    let response;
    worker.__messageListeners[0]({ type, platform: 'EBAY', error: 'test' }, {}, (value) => {
      response = value;
    });
    assert.equal(response.ok, true);
  }
});

test('Vendoo content script answers Studio commands without adding page elements', async () => {
  const listeners = new Set();
  const messages = [];
  const context = vm.createContext({
    console: { log() {}, warn() {}, error() {} },
    document: {
      readyState: 'complete',
      documentElement: {},
      createElement() { assert.fail('Unexpected extension interface'); },
    },
    window: { location: new URL('https://web.vendoo.co/app/item/draft123') },
    chrome: { runtime: {
      lastError: null,
      sendMessage(message, callback) { messages.push(message); callback?.(); },
      onMessage: {
        addListener(listener) { listeners.add(listener); },
        removeListener(listener) { listeners.delete(listener); },
      },
    } },
    MutationObserver: class { observe() {} disconnect() {} },
    setInterval() { return 1; },
    clearInterval() {},
  });
  for (const file of ['content-script-version.js', 'content-scripts/vendoo.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), context);
  }
  assert.equal(messages[0].type, 'ENSURE_STUDIO_CONNECTION');
  assert.equal(listeners.size, 1);
  const listener = [...listeners][0];
  let pong;
  listener({ type: 'PING' }, {}, (value) => { pong = value; });
  assert.equal(pong.ok, true);
  assert.equal(pong.platform, 'VENDOO');
  const upload = await new Promise((resolve) => {
    listener({ type: 'UPLOAD_PHOTOS', files: [] }, {}, resolve);
  });
  assert.equal(upload.ok, true);
  assert.equal(upload.count, 0);
  context.window.__vendooStudioBridgeCleanup();
  assert.equal(listeners.size, 0);
});
