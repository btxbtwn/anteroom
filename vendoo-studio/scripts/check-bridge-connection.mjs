// Exercise real Chrome permission storage and popup approval on disposable data.
import assert from 'node:assert/strict';
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const temporary = await mkdtemp(path.join(tmpdir(), 'anteroom-bridge-connection-'));
const extension = path.join(temporary, 'bridge');
const profile = path.join(temporary, 'profile');
const origin = 'https://myanteroom.com';
const account = '11111111-1111-4111-8111-111111111111';
let context;
async function launch() {
  context = await chromium.launchPersistentContext(profile, {
    channel: 'chromium', headless: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  // The trusted origin is served entirely from memory; no real site is contacted.
  await context.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith('chrome-extension://')) return route.continue();
    return url.startsWith(origin + '/')
      ? route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Connection test</title>' }) : route.abort();
  });
  await worker();
}
async function worker() {
  return context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', { timeout: 15000 });
}
async function page() {
  const tab = await context.newPage();
  await tab.goto(origin);
  return tab;
}
async function request(tab, action, owner = account) {
  return await tab.evaluate(({ action, owner }) => new Promise((resolve, reject) => {
    const requestId = crypto.randomUUID();
    const timer = setTimeout(() => { window.removeEventListener('message', reply); reject(new Error('Bridge did not reply')); }, 10000);
    function reply(event) {
      if (event.source !== window || event.origin !== location.origin || event.data?.source !== 'anteroom-extension' || event.data.requestId !== requestId) return;
      clearTimeout(timer); window.removeEventListener('message', reply); resolve(event.data);
    }
    window.addEventListener('message', reply);
    window.postMessage({ source: 'anteroom-cloud', version: 1, requestId, action, accountId: owner,
      ...(action === 'connect' ? { email: 'seller@example.com' } : {}) }, location.origin);
  }), { action, owner });
}
try {
  await cp(fileURLToPath(new URL('../../vendoo-extension/', import.meta.url)), extension, { recursive: true });
  const entry = path.join(extension, 'background.js');
  await writeFile(entry, `
globalThis.fetch = async () => { throw new Error('Network disabled in connection check'); };
globalThis.WebSocket = class { static CONNECTING=0; static OPEN=1; static CLOSING=2; static CLOSED=3; readyState=3; close() {} };
` + await readFile(entry, 'utf8'));
  await launch();
  let tab = await page();
  assert.equal((await request(tab, 'status')).state, 'disconnected');
  const popup = context.waitForEvent('page', { predicate: tab => tab.url().includes('/cloud-connect.html') });
  assert.equal((await request(tab, 'connect')).state, 'pending');
  const approval = await popup;
  const approved = approval.waitForEvent('close', { timeout: 15000 });
  await approval.waitForFunction(() => !document.getElementById('approve').disabled, null, { timeout: 10000 }).catch(async error => {
    throw new Error(`Approval window: ${await approval.locator('main').innerText()}`, { cause: error });
  });
  await approval.getByRole('button', { name: 'Connect browser', exact: true }).click();
  await approved;
  console.log('Approved once in the real extension popup.');
  assert.equal((await request(tab, 'status')).state, 'connected');
  await tab.close();
  tab = await page();
  assert.equal((await request(tab, 'status')).state, 'connected', 'Closing the tab must retain approval');
  await tab.reload();
  assert.equal((await request(tab, 'status')).state, 'connected', 'Reloading the page must retain approval');
  await context.close(); context = null;
  await launch();
  tab = await page();
  assert.equal((await request(tab, 'status')).state, 'connected', 'Restarting Chrome must retain approval');
  await context.close(); context = null;
  // Install a different version at the same extension path/profile, as an app update does.
  const manifestPath = path.join(extension, 'manifest.json');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  manifest.version = '99.0.0';
  await writeFile(manifestPath, JSON.stringify(manifest));
  await launch();
  tab = await page();
  const restored = await request(tab, 'status');
  assert.equal(restored.extensionVersion, '99.0.0');
  assert.equal(restored.state, 'connected', 'Updating the extension must retain approval');
  const second = await page();
  assert.equal((await request(second, 'status')).state, 'connected');
  assert.equal((await request(tab, 'disconnect')).state, 'disconnected');
  assert.equal((await request(second, 'status')).state, 'disconnected', 'Disconnect must revoke every tab');
  assert.equal((await request(second, 'status', '22222222-2222-4222-8222-222222222222')).state, 'disconnected');
  console.log('Chrome retained approved account permission across tab/page reloads, browser restarts and a Bridge version update; disconnect revoked all tabs.');
} finally {
  await context?.close();
  await rm(temporary, { recursive: true, force: true });
}
