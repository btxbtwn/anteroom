var STUDIO_EXTENSION_BUILD = null;
try { importScripts('studio-build.js'); } catch {}
const STUDIO_URL = typeof STUDIO_API_ORIGIN === 'string' ? STUDIO_API_ORIGIN : 'http://127.0.0.1:4318';
if (!/^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(STUDIO_URL)) throw new Error('Anteroom Bridge requires a loopback API address.');
// chrome.storage.local holds the pairing token, the web approvals and the action journals.
// Chrome lets content scripts read it by default; only the worker and the extension's own
// page need it, so keep it to them (storage.session already is).
chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' })
  .catch(err => console.error(`[BG-Studio] Could not restrict storage: ${err?.message || err}`));
// What this Bridge can run, sent when it connects. Anteroom checks these rather
// than the exact build, so an older copy keeps working until a change needs
// something new. Add a name here when the Bridge gains a command an older copy
// could not run, and require it in Anteroom.
const BRIDGE_CAPABILITIES = ['marketplace.actions', 'marketplace.sales_history', 'marketplace.promotions', 'vendoo.drafts'];
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8000;
// A socket that stays up this long counts as a good connection, and only then
// does the retry backoff reset. Clearing it on open let a worker that pairs and
// drops straight away reconnect every 500ms indefinitely, which starves Studio.
const STABLE_CONNECTION_MS = 10000;
const HEARTBEAT_MS = 20000;
const RECONNECT_ALARM = 'studio-reconnect';
// Chrome's minimum repeating alarm period; wakes the MV3 worker if setTimeout died with it.
const RECONNECT_ALARM_PERIOD_MINUTES = 0.5;
const RELOAD_GENERATION_KEY = 'studio_reload_generation';

let ws = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let paired = false;
let reconnectAttempt = 0;
let socketOpenedAt = 0;
// Set when Studio says another copy of the bridge already holds the socket.
// Two copies that both reconnect close each other's socket every half second,
// and every request in flight is lost with it.
let duplicateBridge = false;

importScripts('background/marketplace-tabs.js');
importScripts('background/sold-listings.js');
importScripts('background/depop-native.js');
importScripts('background/marketplace-fetch.js');
importScripts('background/marketplace-catalog.js');
importScripts('background/mercari-native.js');
importScripts('background/marketplace-network.js');
importScripts('background/poshmark-native.js');
importScripts('background/native-marketplaces.js');
importScripts('background/vendoo-api.js');
importScripts('background/marketplace-delete.js');
importScripts('background/marketplace-promotions.js');
importScripts('background/marketplace-likes.js');
importScripts('background/marketplace-sessions.js');
importScripts('background/marketplace-sales.js');
importScripts('background/marketplace-order-history.js');
importScripts('background/marketplace-import.js');
importScripts('background/marketplace-inspection.js');
importScripts('background/direct-check.js');
importScripts('background/native-forms.js');

function log(msg) {
  console.log(`[BG-Studio] ${msg}`);
}

function error(msg) {
  console.error(`[BG-Studio] ${msg}`);
}

async function getPairingToken() {
  const stored = await chrome.storage.local.get('studio_pairing_token');
  return stored.studio_pairing_token || null;
}

async function setPairingToken(token) {
  await chrome.storage.local.set({ studio_pairing_token: token });
}

function send(message) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message));
  }
}

async function fetchStudioPairingToken() {
  const resp = await fetch(`${STUDIO_URL}/api/extension/pairing-token`);
  if (!resp.ok) throw new Error(`pairing-token HTTP ${resp.status}`);
  const data = await resp.json();
  const token = String(data?.token || '').trim();
  if (!token || token === 'direct') throw new Error('Anteroom returned an invalid pairing token');
  await setPairingToken(token);
  return token;
}

async function resolvePairingToken() {
  const stored = await getPairingToken();
  if (stored && stored !== 'direct') return stored;
  return fetchStudioPairingToken();
}

function socketIsLive() {
  return Boolean(ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING));
}

function socketIsOpen() {
  return Boolean(ws && ws.readyState === WebSocket.OPEN);
}

function discardSocket() {
  if (!ws) return;
  try {
    ws.onopen = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.onclose = null;
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  } catch (_) {}
  ws = null;
  socketOpenedAt = 0;
}

function ensureReconnectAlarm() {
  try {
    chrome.alarms.create(RECONNECT_ALARM, { periodInMinutes: RECONNECT_ALARM_PERIOD_MINUTES });
  } catch (err) {
    error(`Reconnect alarm failed: ${err && err.message ? err.message : err}`);
  }
}

async function ensureStudioConnection({ resetBackoff = false, fromAlarm = false } = {}) {
  ensureReconnectAlarm();
  if (duplicateBridge) {
    // Only the reconnect alarm tries again: a fast retry here would restart the
    // fight with the copy Studio is actually using.
    if (!fromAlarm) return { ok: false, duplicateBridge: true };
    duplicateBridge = false;
  }
  if (resetBackoff) {
    reconnectAttempt = 0;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }
  if (paired && socketIsOpen()) return { ok: true, paired: true };
  if (socketIsOpen() && !paired) {
    await sendIdent();
    return { ok: true, paired };
  }
  if (socketIsLive()) return { ok: true, paired: false, connecting: true };
  connect();
  return { ok: true, paired: false, connecting: true };
}

function connect() {
  if (socketIsLive()) {
    return;
  }
  discardSocket();

  log(`Connecting to ${STUDIO_URL}/api/extension/ws...`);

  try {
    ws = new WebSocket(`${STUDIO_URL.replace(/^http:/, 'ws:')}/api/extension/ws`);
  } catch (e) {
    error(`WebSocket creation failed: ${e.message}`);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    log('WebSocket connected');
    socketOpenedAt = Date.now();
    startHeartbeat();
    sendIdent();
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (isDuplicateCopyError(msg)) {
        standDownForDuplicateCopy();
        return;
      }
      if (msg.type === 'error' && /invalid pairing token/i.test(String(msg.message || ''))) {
        fetchStudioPairingToken().then(() => sendIdent()).catch((err) => {
          error(`Pairing token refresh failed: ${err.message}`);
          discardSocket();
          paired = false;
          stopHeartbeat();
          scheduleReconnect();
        });
        return;
      }
      Promise.resolve(handleStudioMessage(msg)).catch((err) => {
        error(`Failed to handle message: ${err.message}`);
      });
    } catch (e) {
      error(`Failed to parse message: ${e.message}`);
    }
  };

  ws.onclose = (event) => {
    const lived = socketOpenedAt ? Date.now() - socketOpenedAt : 0;
    socketOpenedAt = 0;
    if (lived >= STABLE_CONNECTION_MS) reconnectAttempt = 0;
    log(`WebSocket closed after ${lived}ms code=${event.code}`);
    ws = null;
    paired = false;
    stopHeartbeat();
    scheduleReconnect();
  };

  ws.onerror = () => {
    error('WebSocket error');
  };
}

async function sendIdent() {
  let token;
  try {
    token = await resolvePairingToken();
  } catch (err) {
    error(`Pairing token is unavailable: ${err.message}`);
    discardSocket();
    paired = false;
    stopHeartbeat();
    scheduleReconnect();
    return;
  }
  const stored = await chrome.storage.local.get(RELOAD_GENERATION_KEY);
  send({
    version: 1,
    type: 'extension.ready',
    message_id: crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36),
    sent_at: new Date().toISOString(),
    payload: identPayload(token, stored[RELOAD_GENERATION_KEY] || null),
  });
}

function identPayload(token, reloadGeneration) {
  const build = typeof STUDIO_EXTENSION_BUILD === 'string' ? STUDIO_EXTENSION_BUILD.trim() : '';
  return {
    token: token || '',
    version: chrome.runtime.getManifest().version,
    build: build || null,
    capabilities: BRIDGE_CAPABILITIES,
    reload_generation: reloadGeneration || null,
  };
}

async function handleExtensionReload(generation) {
  if (generation) {
    await chrome.storage.local.set({ [RELOAD_GENERATION_KEY]: generation });
  }
  log('Reloading extension after Studio update');
  chrome.runtime.reload();
}

/** Studio said a different copy of this bridge already holds the socket. */
function isDuplicateCopyError(msg) {
  if (!msg || msg.type !== 'error') return false;
  return msg.duplicate_bridge === true || /another copy of the anteroom bridge/i.test(String(msg.message || ''));
}

// Two copies that both reconnect close each other's socket every half second and
// lose every reply in flight. The refused one waits for the reconnect alarm
// instead of retrying, so the copy Studio is using keeps a still socket.
function standDownForDuplicateCopy() {
  error('Another copy of the Anteroom Bridge is connected; standing down until the next reconnect check.');
  duplicateBridge = true;
  discardSocket();
  paired = false;
  stopHeartbeat();
}

function scheduleReconnect() {
  ensureReconnectAlarm();
  if (reconnectTimer) return;
  const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt), RECONNECT_MAX_MS);
  reconnectAttempt++;
  log(`Reconnecting in ${delay}ms (attempt ${reconnectAttempt})`);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm || alarm.name !== RECONNECT_ALARM) return;
  ensureStudioConnection({ fromAlarm: true });
});

chrome.runtime.onInstalled.addListener(() => {
  ensureReconnectAlarm();
  ensureStudioConnection({ resetBackoff: true });
});

chrome.runtime.onStartup.addListener(() => {
  ensureReconnectAlarm();
  ensureStudioConnection({ resetBackoff: true });
});

function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    send({ version: 1, type: 'pong', message_id: Date.now().toString(36), sent_at: new Date().toISOString() });
  }, HEARTBEAT_MS);
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

async function handleStudioMessage(msg) {
  const type = msg.type || '';

  switch (type) {
    case 'connection.accepted':
      log('Paired with Studio');
      paired = true;
      duplicateBridge = false;
      break;

    case 'job.vendoo_api':
      handleVendooApiMessage(msg);
      break;
    case 'marketplace.execute':
    case 'marketplace.result': {
      await handleNativeMarketplaceMessage(msg);
      break;
    }

    case 'marketplace.inspect': {
      await handleMarketplaceInspection(msg);
      break;
    }

    case 'marketplace.connect': {
      await handleMarketplaceConnect(msg);
      break;
    }

    case 'marketplace.sessions': {
      await handleMarketplaceSessions(msg);
      break;
    }

    case 'marketplace.direct_check': {
      await handleMarketplaceDirectCheck(msg);
      break;
    }

    case 'marketplace.form': {
      await handleNativeMarketplaceForm(msg);
      break;
    }

    case 'marketplace.sales': {
      await handleMarketplaceSales(msg);
      break;
    }

    case 'marketplace.sales_history': {
      await handleMarketplaceOrderHistory(msg);
      break;
    }

    case 'marketplace.import.page':
    case 'marketplace.import.listing': {
      await handleMarketplaceImport(msg);
      break;
    }

    case 'marketplace.record': {
      await handleMarketplaceRecord(msg);
      break;
    }

    case 'marketplace.delete': {
      await handleMarketplaceDelete(msg);
      break;
    }

    case 'marketplace.like_events': {
      sendBrowserResult(msg, await readMarketplaceLikes(msg.payload || {}));
      break;
    }

    case 'marketplace.promotion_check':
    case 'marketplace.promote':
    case 'marketplace.promotion_result': {
      await handleMarketplacePromotion(msg);
      break;
    }

    case 'job.cancel': {
      const jobId = msg.job_id || msg.payload?.job_id;
      if (!jobId) return;
      await cancelNativeMarketplaceOperation(jobId);
      break;
    }

    case 'ping':
      send({
        version: 1,
        type: 'pong',
        message_id: Date.now().toString(36),
        sent_at: new Date().toISOString(),
      });
      break;

    case 'market.sold_listings': {
      const queries = Array.isArray(msg.payload?.queries) ? msg.payload.queries.map(String) : [];
      const result = msg.payload?.marketplace === 'ebay'
        ? await readEbaySoldListings(queries)
        : { ok: false, results: {}, error: 'Unsupported marketplace' };
      send({ type: 'market.sold_listings_result', payload: { ...result, request_id: msg.payload?.request_id } });
      break;
    }

    case 'extension.reload':
      await handleExtensionReload(msg.payload?.generation);
      break;
  }
}

// Initialize
(async () => {
  ensureReconnectAlarm();
  await ensureStudioConnection({ resetBackoff: true });
})();

// Hosted connection permissions are isolated from the desktop socket protocol.
importScripts('cloud-marketplace-protocol.js');
importScripts('background/cloud-marketplace-forms.js');
importScripts('background/cloud-marketplace-actions.js');
importScripts('background/cloud-approvals.js');
importScripts('background/cloud-connection.js');
