// Exercise Chrome's rule compiler, which the worker unit tests stub.
import assert from 'node:assert/strict';
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const source = fileURLToPath(new URL('../../vendoo-extension/', import.meta.url));
const temporary = await mkdtemp(path.join(tmpdir(), 'anteroom-bridge-rules-'));
let context;
try {
  const extension = path.join(temporary, 'bridge');
  await cp(source, extension, { recursive: true });
  const entry = path.join(extension, 'background.js');
  // No account, marketplace, or real local-app requests during this check.
  const offline = `
globalThis.fetch = async () => { throw new Error('Network disabled in Bridge rule check'); };
globalThis.WebSocket = class {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  readyState = 3;
  close() {}
};
`;
  await writeFile(entry, offline + await readFile(entry, 'utf8'));
  context = await chromium.launchPersistentContext(path.join(temporary, 'profile'), {
    channel: 'chromium', headless: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', { timeout: 15000 });
  const result = await worker.evaluate(async () => {
    let reason;
    const update = chrome.declarativeNetRequest.updateSessionRules.bind(chrome.declarativeNetRequest);
    chrome.declarativeNetRequest.updateSessionRules = async options => {
      try { return await update(options); }
      catch (error) { reason = error.message; throw error; }
    };
    try {
      await ensureMarketplaceRequestContext();
      await ensureMarketplaceRequestContext();
      return { ok: true, count: (await chrome.declarativeNetRequest.getSessionRules()).length };
    } catch (error) {
      return { ok: false, error: reason || error.message };
    }
  });
  assert.equal(result.ok, true, result.error);
  assert.ok(result.count > 0, 'Bridge must install its marketplace network rules');
  console.log(`Chrome accepted all ${result.count} Bridge network rules.`);
} finally {
  await context?.close();
  await rm(temporary, { recursive: true, force: true });
}
