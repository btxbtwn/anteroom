const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { ORIGIN } = require('../background/cloud-connection');
const ACCOUNT = '11111111-1111-4111-8111-111111111111';
const REQUEST = '22222222-2222-4222-8222-222222222222';
const code = fs.readFileSync(path.join(__dirname, '../content-scripts/cloud-connection.js'), 'utf8');
function setup() {
  let listener, probe;
  const sent = [], replies = [];
  const window = { addEventListener: (_, fn) => { listener = fn; }, postMessage: (...args) => replies.push(args) };
  window.top = window;
  const chrome = { runtime: { id: 'bridge', onMessage: { addListener: fn => { probe = fn; } }, sendMessage: async msg => { sent.push(msg); return { ok: true, state: 'pending', extensionVersion: '0.2.38', private: 'must-not-return' }; } } };
  vm.runInNewContext(code, { window, location: { origin: ORIGIN }, chrome });
  const data = { source: 'anteroom-cloud', version: 1, requestId: REQUEST, action: 'connect', accountId: ACCOUNT, email: 'alice@example.com' };
  return { sent, replies, data, window, emit: event => listener({ source: window, origin: ORIGIN, data, ...event }), probe: (...args) => probe(...args) };
}

test('content script copies only the connection contract and strips private response fields', async () => {
  const s = setup(); s.emit({}); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(JSON.parse(JSON.stringify(s.sent)), [{ type: 'cloud.connection', action: 'connect', accountId: ACCOUNT, email: 'alice@example.com' }]);
  assert.equal(JSON.stringify(s.replies).includes('private'), false);
  assert.equal(s.replies[0][1], ORIGIN);
  let current;
  s.probe({ type: 'cloud.connection.probe', accountId: ACCOUNT }, { id: 'bridge' }, reply => { current = reply.current; });
  assert.equal(current, true);
});

test('page cannot send credentials, marketplace commands or embedded/cross-origin requests', async () => {
  const s = setup();
  s.emit({ origin: 'https://evil.example' }); s.emit({ source: {} });
  s.emit({ data: { ...s.data, access_token: 'secret' } });
  s.emit({ data: { ...s.data, action: 'publish' } });
  s.emit({ data: { ...s.data, accountId: 'invalid' } });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(s.sent, []);
});

test('late disconnect from an old account does not invalidate the new document identity', async () => {
  const s = setup();
  const bob = '33333333-3333-4333-8333-333333333333';
  const status = { ...s.data, action: 'status', accountId: bob }; delete status.email;
  s.emit({ data: status });
  const disconnect = { ...s.data, action: 'disconnect' }; delete disconnect.email;
  s.emit({ data: disconnect });
  let current;
  s.probe({ type: 'cloud.connection.probe', accountId: bob }, { id: 'bridge' }, reply => { current = reply.current; });
  assert.equal(current, true);
});
