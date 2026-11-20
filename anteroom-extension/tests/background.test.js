const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWorker, call } = require('./worker-context');

const worker = loadWorker();

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
