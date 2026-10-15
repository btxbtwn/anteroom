const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const { loadWorker } = require('./worker-context');

function navigationWorker() {
  const ctx = loadWorker();
  ctx.chrome.debugger = {};
  vm.runInContext(`
    browserSession = { jobId: 'draft', tabId: 7 };
    previewTabId = 7;
    previewJobId = 'draft';
    previewAttached = true;
    previewPollTimer = 1;
    sendBrowserResult = (_msg, result) => { self.result = result; };
  `, ctx);
  return ctx;
}

test('Back and Forward navigate Chrome history, including same-document entries', async () => {
  const ctx = navigationWorker();
  const calls = [];
  ctx.chrome.debugger.sendCommand = async (_tab, method, params) => {
    calls.push({ method, params });
    return { currentIndex: 1, entries: [{ id: 11 }, { id: 12 }, { id: 13 }] };
  };
  await ctx.handleBrowserMessage({ type: 'browser.navigate', job_id: 'draft', payload: { direction: 'back' } });
  assert.equal(calls[1].method, 'Page.navigateToHistoryEntry');
  assert.equal(calls[1].params.entryId, 11);
  await ctx.handleBrowserMessage({ type: 'browser.navigate', job_id: 'draft', payload: { direction: 'forward' } });
  assert.equal(calls[3].params.entryId, 13);
});

test('Navigation at the history boundary does not reload or create a page', async () => {
  const ctx = navigationWorker();
  const calls = [];
  ctx.chrome.debugger.sendCommand = async (_tab, method) => {
    calls.push(method);
    return { currentIndex: 0, entries: [{ id: 11 }] };
  };
  await ctx.handleBrowserMessage({ type: 'browser.navigate', job_id: 'draft', payload: { direction: 'back' } });
  assert.deepEqual(calls, ['Page.getNavigationHistory']);
  assert.equal(ctx.result.ok, true);
});

test('Seller navigation is refused during automation or for another job', async () => {
  for (const type of ['browser.navigate']) {
    const ctx = navigationWorker();
    let commands = 0;
    ctx.chrome.debugger.sendCommand = async () => { commands++; };
    vm.runInContext("activeJob = { job_id: 'draft', tabId: 7 };", ctx);
    await ctx.handleBrowserMessage({ type, job_id: 'draft', payload: { direction: 'back', width: 390, height: 600 } });
    assert.equal(ctx.result.ok, false);
    assert.equal(commands, 0);
    vm.runInContext('activeJob = null;', ctx);
    await ctx.handleBrowserMessage({ type, job_id: 'other', payload: {} });
    assert.equal(ctx.result.ok, false);
    assert.equal(commands, 0);
  }
});

test('Repeated input uses captured viewport without querying Chrome again', async () => {
  const ctx = navigationWorker();
  vm.runInContext('browserSession.capturedViewport = { width: 390, height: 600 };', ctx);
  const calls = [];
  ctx.chrome.debugger.sendCommand = async (_tab, method, params) => calls.push({ method, params });
  await ctx.handleBrowserMessage({ type: 'browser.input', job_id: 'draft', payload: { events: [{ kind: 'mouse', type: 'mousePressed', x_ratio: 0.5, y_ratio: 0.5, button: 'left' }] } });
  assert.deepEqual(calls.map(c => c.method), ['Input.dispatchMouseEvent']);
  assert.equal(calls[0].params.x, 195);
  assert.equal(calls[0].params.y, 300);
});

test('Screenshot capture never overlaps and drops unchanged frames', async () => {
  const ctx = navigationWorker();
  const captures = [];
  const frames = [];
  ctx.sendPreviewFrame = (...args) => frames.push(args);
  ctx.tabPreviewInfo = async () => ({ viewportWidth: 390, viewportHeight: 600 });
  ctx.captureDebuggerScreenshot = () => new Promise(resolve => captures.push(resolve));
  ctx.startDebuggerScreenshotPoll(7, 'draft');
  const second = vm.runInContext("previewCaptureNow", ctx);
  await second();
  assert.equal(captures.length, 1);
  captures[0]('frame');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(frames.length, 1);
  const next = second();
  captures[1]('frame');
  await next;
  assert.equal(frames.length, 1);
});
