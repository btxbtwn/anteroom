const assert = require('node:assert/strict');
const test = require('node:test');
const { loadWorker, call } = require('./worker-context');

test('the Bridge tells Anteroom what it can run when it connects', () => {
  const ready = call(loadWorker(), 'identPayload', 'token', null);
  assert.deepEqual(ready.capabilities, ['marketplace.actions']);
});
