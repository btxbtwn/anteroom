const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { loadWorker } = require('./worker-context');

test('the QA bridge uses its stamped loopback port without changing the production default', () => {
  assert.equal(vm.runInContext('STUDIO_URL', loadWorker()), 'http://127.0.0.1:4318');
  assert.equal(vm.runInContext('STUDIO_URL', loadWorker({ apiOrigin: 'http://127.0.0.1:4388' })), 'http://127.0.0.1:4388');
});

test('bridge bootstrap rejects a public or non-loopback server', () => {
  for (const apiOrigin of ['https://example.com', 'http://0.0.0.0:4388', 'http://127.0.0.1:4388/private']) {
    assert.throws(() => loadWorker({ apiOrigin }), /loopback/);
  }
});

test('the live connection goes to the same stamped port as requests', () => {
  for (const [apiOrigin, expected] of [
    [undefined, 'ws://127.0.0.1:4318/api/extension/ws'],
    ['http://127.0.0.1:4388', 'ws://127.0.0.1:4388/api/extension/ws'],
  ]) {
    const context = loadWorker(apiOrigin ? { apiOrigin } : {});
    const opened = [];
    context.WebSocket = function WebSocket(url) {
      opened.push(url);
      this.readyState = 0;
      this.close = () => { this.readyState = 3; };
    };
    vm.runInContext('connect()', context);
    assert.deepEqual(opened, [expected]);
  }
});
