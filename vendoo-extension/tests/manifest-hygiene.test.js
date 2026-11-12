const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadWorker } = require('./worker-context');

const ROOT = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'manifest.json'), 'utf8'));

test('content scripts cannot read the worker\'s storage: pairing token, approvals, journals', () => {
  const c = loadWorker();
  assert.deepEqual(c.__storageAccess.map(options => ({ ...options })), [{ accessLevel: 'TRUSTED_CONTEXTS' }]);
  // Nothing outside the worker and the extension's own page reads chrome.storage.
  for (const file of fs.readdirSync(path.join(ROOT, 'content-scripts'))) {
    assert.doesNotMatch(fs.readFileSync(path.join(ROOT, 'content-scripts', file), 'utf8'), /chrome\.storage/, file);
  }
});

test('Chrome is new enough for the worker\'s 30-second reconnect alarm', () => {
  // A repeating alarm under one minute needs Chrome 120; older Chrome raises it to a minute.
  const source = fs.readFileSync(path.join(ROOT, 'background.js'), 'utf8');
  const period = Number(/RECONNECT_ALARM_PERIOD_MINUTES = ([\d.]+)/.exec(source)[1]);
  assert.ok(period < 1);
  assert.ok(Number(manifest.minimum_chrome_version) >= 120, manifest.minimum_chrome_version);
});

test('host permissions have callers', () => {
  // Removed with their last callers: Depop's partner API and Vendoo's old API host.
  for (const host of ['https://api.web.vendoo.co/*', 'https://api.depop.com/*']) {
    assert.ok(!manifest.host_permissions.includes(host), host);
  }
});

test('the description says what the Bridge does, within Chrome\'s limit', () => {
  assert.ok(manifest.description.length <= 132, `${manifest.description.length} characters`);
  assert.match(manifest.description, /publish/i);
  assert.match(manifest.description, /offer/i);
});
