const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker, call } = require('./worker-context');

const worker = loadWorker();
const commands = [];
// The harness stubs chrome.debugger with a proxy that swallows assignments.
worker.chrome.debugger = {
  attach: async () => {},
  detach: async () => {},
  sendCommand: async (target, method, params) => {
    commands.push({ tabId: target.tabId, method, params: params || null });
    return {};
  },
};

function attachPreview(tabId, jobId) {
  commands.length = 0;
  vm.runInContext(
    `previewAttached = true; previewTabId = ${tabId}; previewJobId = '${jobId}'; previewViewport = null;`,
    worker,
  );
}

function sent(method) {
  return JSON.parse(JSON.stringify(commands.filter((command) => command.method === method)));
}

test('a phone viewport is rounded and bounded, and anything else means desktop', () => {
  assert.deepEqual(call(worker, 'browserViewportFrom', { width: 390.4, height: 640, scale: 2 }), { width: 390, height: 640, scale: 2 });
  assert.deepEqual(call(worker, 'browserViewportFrom', { width: 4000, height: 9000, scale: 9 }), { width: 1024, height: 2000, scale: 3 });
  assert.equal(call(worker, 'browserViewportFrom', { width: 100, height: 640 }), null);
  assert.equal(call(worker, 'browserViewportFrom', { width: 'wide', height: 640 }), null);
  assert.equal(call(worker, 'browserViewportFrom', null), null);
});

test('the phone layout is applied once and captured from the surface', async () => {
  attachPreview(7, 'job-1');
  const viewport = { width: 390, height: 640, scale: 2 };
  worker.__viewport = viewport;
  assert.equal(await vm.runInContext('setPreviewViewport(7, __viewport)', worker), true);
  assert.equal(await vm.runInContext('setPreviewViewport(7, __viewport)', worker), true);
  assert.deepEqual(sent('Emulation.setDeviceMetricsOverride'), [{
    tabId: 7,
    method: 'Emulation.setDeviceMetricsOverride',
    params: { width: 390, height: 640, deviceScaleFactor: 2, mobile: true },
  }]);
  assert.equal(sent('Emulation.setTouchEmulationEnabled')[0].params.enabled, true);

  await vm.runInContext('captureDebuggerScreenshot(7)', worker);
  assert.deepEqual(sent('Page.captureScreenshot').map((command) => command.params.fromSurface), [true]);
});

test('a preview started for a fill puts the tab back on the desktop layout', async () => {
  attachPreview(7, 'job-1');
  worker.__viewport = { width: 390, height: 640, scale: 2 };
  await vm.runInContext('setPreviewViewport(7, __viewport)', worker);
  commands.length = 0;
  await vm.runInContext("startJobPreview(7, 'job-1')", worker);
  assert.equal(sent('Emulation.clearDeviceMetricsOverride').length, 1);
  assert.equal(sent('Emulation.setTouchEmulationEnabled')[0].params.enabled, false);
  assert.equal(vm.runInContext('previewViewport', worker), null);
});

test('the phone layout waits while Studio is filling and returns afterwards', async () => {
  attachPreview(7, 'job-1');
  vm.runInContext(
    "browserSession = { jobId: 'job-1', tabId: 7, itemId: null, viewport: { width: 390, height: 640, scale: 2 } };"
    + " previewPollTimer = 1; activePatch = { job_id: 'job-1', tabId: 7 };",
    worker,
  );
  await vm.runInContext('resumeBrowserSessionPreview()', worker);
  assert.equal(sent('Emulation.setDeviceMetricsOverride').length, 0);
  assert.equal(call(worker, 'browserViewportSettled', { viewport: { width: 390, height: 640, scale: 2 } }), false);

  vm.runInContext('activePatch = null;', worker);
  await vm.runInContext('resumeBrowserSessionPreview()', worker);
  assert.equal(sent('Emulation.setDeviceMetricsOverride').length, 1);
  assert.equal(call(worker, 'browserViewportSettled', { viewport: { width: 390, height: 640, scale: 2 } }), true);
  vm.runInContext('browserSession = null; previewPollTimer = null;', worker);
});

test('touch input lands at the finger position and lifts with no points', async () => {
  attachPreview(7, 'job-1');
  worker.__events = [
    { kind: 'touch', type: 'touchStart', x_ratio: 0.5, y_ratio: 0.25 },
    { kind: 'touch', type: 'touchMove', x_ratio: 2, y_ratio: 0.5 },
    { kind: 'touch', type: 'touchEnd' },
    { kind: 'touch', type: 'pinch', x_ratio: 0.5, y_ratio: 0.5 },
  ];
  await vm.runInContext('runBrowserInput(7, { width: 400, height: 800 }, __events)', worker);
  assert.deepEqual(sent('Input.dispatchTouchEvent').map((command) => command.params), [
    { type: 'touchStart', touchPoints: [{ x: 200, y: 200 }], modifiers: 0 },
    { type: 'touchMove', touchPoints: [{ x: 400, y: 400 }], modifiers: 0 },
    { type: 'touchEnd', touchPoints: [], modifiers: 0 },
  ]);
});
