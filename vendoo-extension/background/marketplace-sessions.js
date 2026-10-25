const MARKETPLACE_SIGN_IN = {
  depop: 'https://www.depop.com/sellinghub/drafts/',
  poshmark: 'https://poshmark.com/feed',
  mercari: 'https://www.mercari.com/mypage/',
  sellwild: 'https://sellwild.com/',
};

// Runs only during an explicit sign-in/reconnect. Session values stay inside
// Chrome; the API receives only readiness and diagnostic field names.
async function readMarketplaceSignIn(marketplace) {
  if (marketplace === 'depop') {
    const cookie = document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith('access_token='));
    const token = cookie ? decodeURIComponent(cookie.slice(13)) : null;
    let status = null;
    if (token) {
      const response = await fetch('https://webapi.depop.com/presentation/api/v1/drafts/?limit=24&sort_by=createdAt', {
        headers: { Accept: 'application/json', Authorization: `Bearer ${token}` }, credentials: 'omit', signal: AbortSignal.timeout(10000),
      });
      status = response.status;
      await response.body?.cancel();
    }
    return { access_token: token, page_read_status: status };
  }
  if (marketplace === 'mercari') {
    const shapes = {};
    for (const key of Object.keys(localStorage)) {
      try {
        const value = JSON.parse(localStorage.getItem(key));
        if (value && typeof value === 'object') shapes[key] = Object.keys(value).slice(0, 30);
      } catch {}
    }
    const databases = await indexedDB.databases();
    const databaseStores = {};
    for (const database of databases) {
      if (!database.name) continue;
      try {
        const db = await new Promise((resolve, reject) => { const request = indexedDB.open(database.name); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
        databaseStores[database.name] = [...db.objectStoreNames];
        db.close();
      } catch {}
    }
    return { page_path: location.pathname, sign_in_visible: [...document.querySelectorAll('a,button')].some(element => element.getClientRects().length && /^(log in|sign in)$/i.test(element.textContent.trim())),
      storage_keys: Object.keys(localStorage), storage_shapes: shapes, session_keys: Object.keys(sessionStorage), database_stores: databaseStores,
      script_hosts: [...new Set([...document.scripts].map(script => script.src).filter(Boolean).map(src => new URL(src).hostname))],
      script_urls: [...document.scripts].map(script => script.src).filter(src => {
        try { const url = new URL(src); return (url.origin === location.origin || url.hostname === 'u-web-assets.mercdn.net') && /\.js$/.test(url.pathname); } catch { return false; }
      }).map(src => { const url = new URL(src); return url.origin + url.pathname; }).slice(0, 60),
      api_urls: [...new Set(performance.getEntriesByType('resource').map(entry => {
        const url = new URL(entry.name); return /\/v1\/api$/.test(url.pathname) ? url.origin + url.pathname : null;
      }).filter(Boolean))] };
  }
  return {};
}

function captureMercariSignIn(tabId) {
  const headers = {};
  const operations = {};
  const bodyShapes = new Set();
  const requests = new Set();
  const capture = source => {
    for (const [name, value] of Object.entries(source || {})) {
      const key = name.toLowerCase();
      if (['authorization', 'x-csrf-token', 'x-platform', 'x-app-version', 'x-client-version', 'x-ld-variants'].includes(key)) headers[key] = value;
    }
  };
  const listener = (source, method, event) => {
    if (source.tabId !== tabId) return;
    if (method === 'Network.requestWillBeSent') {
      const url = new URL(event.request.url);
      if (url.origin !== 'https://www.mercari.com' || url.pathname !== '/v1/api') return;
      requests.add(event.requestId);
      capture(event.request.headers);
      const record = raw => {
        let parsed = {};
        try { parsed = JSON.parse(raw || '{}'); } catch {}
        for (const body of Array.isArray(parsed) ? parsed : [parsed]) {
          Object.keys(body).forEach(key => bodyShapes.add(key));
          if (body.operationName) operations[body.operationName] = {
            hash: body.extensions?.persistedQuery?.sha256Hash, query: body.query,
            variable_keys: Object.keys(body.variables || {}),
          };
        }
      };
      if (event.request.postData) record(event.request.postData);
      else if (event.request.hasPostData) chrome.debugger.sendCommand({ tabId }, 'Network.getRequestPostData', { requestId: event.requestId }).then(value => record(value.postData)).catch(() => {});
    }
    if (method === 'Network.requestWillBeSentExtraInfo' && requests.has(event.requestId)) capture(event.headers);
  };
  chrome.debugger.onEvent.addListener(listener);
  return { headers, operations, bodyShapes, close: () => chrome.debugger.onEvent.removeListener(listener) };
}

async function handleMarketplaceConnect(msg) {
  const marketplace = msg.payload?.marketplace;
  const url = MARKETPLACE_SIGN_IN[marketplace];
  if (!url || activeNativeOperation || activeJob || activePatch) {
    sendBrowserResult(msg, { ok: false, error: url ? 'Wait for the current listing action to finish.' : 'Unsupported marketplace.' });
    return;
  }
  try {
    const origin = new URL(url).origin;
    const existing = (await chrome.tabs.query({ url: `${origin}/*` })).findLast(value => new URL(value.url).pathname === new URL(url).pathname);
    const tab = existing || await openMarketplaceTab(url);
    await chrome.tabs.update(tab.id, { active: true });
    await nativeWait(tab.id, value => value.status === 'complete' && String(value.url || '').startsWith(`${origin}/`), Date.now() + 30000);
    let captured;
    if (marketplace === 'mercari') {
      await chrome.debugger.attach({ tabId: tab.id }, '1.3');
      captured = captureMercariSignIn(tab.id);
      try {
        await chrome.debugger.sendCommand({ tabId: tab.id }, 'Network.enable');
        await chrome.tabs.reload(tab.id);
        const deadline = Date.now() + 12000;
        while ((!captured.headers.authorization || !Object.keys(captured.operations).length) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 250));
        await chrome.storage.session.set({ mercari_session: { headers: captured.headers, operations: captured.operations } });
      } finally {
        captured.close();
        await chrome.debugger.detach({ tabId: tab.id }).catch(() => {});
      }
    }
    const result = (await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: 'MAIN', func: readMarketplaceSignIn, args: [marketplace] }))?.[0]?.result || {};
    if (marketplace === 'depop') {
      const cookie = await chrome.cookies.get({ url: 'https://www.depop.com/', name: 'access_token' });
      if (result.access_token) await chrome.storage.session.set({ depop_session: { access_token: result.access_token } });
      sendBrowserResult(msg, { ok: true, signed_in: Boolean(result.access_token),
        page_read_status: result.page_read_status,
        cookie_matches: Boolean(cookie?.value && decodeURIComponent(cookie.value) === result.access_token) });
    } else sendBrowserResult(msg, { ok: true, ...result,
      ...(captured ? { captured_header_names: Object.keys(captured.headers), operations: captured.operations, body_keys: [...captured.bodyShapes] } : {}) });
  } catch { sendBrowserResult(msg, { ok: false, error: 'Finish signing in in Chrome, then reconnect. Listing actions will not open tabs.' }); }
}

// One-time recording while the seller saves a throwaway draft by hand, so a
// marketplace's own save request can be rebuilt to run without a tab. Keeps
// only request shapes: operation names, GraphQL query text, method and path,
// header names, and value types. Never header values, cookies, listing text,
// photos, or response values.
const MARKETPLACE_RECORD_START = {
  mercari: 'https://www.mercari.com/sell/',
  sellwild: 'https://sellwild.com/sell',
};
const RECORD_LIMIT_MS = 5 * 60 * 1000;

function valueShape(value, depth = 0) {
  if (Array.isArray(value)) return value.length ? [valueShape(value[0], depth + 1)] : [];
  if (value && typeof value === 'object') {
    if (depth >= 6) return 'object';
    return Object.fromEntries(Object.entries(value).slice(0, 60).map(([key, child]) => [key, valueShape(child, depth + 1)]));
  }
  return value === null ? 'null' : typeof value;
}

// GraphQL file uploads send their operation as the multipart "operations"
// part. Keep that part and the other parts' names; never the file bytes.
function recordedMultipart(raw, contentType) {
  const boundary = /boundary=([^;]+)/i.exec(contentType || '')?.[1];
  if (!boundary) return { kind: 'multipart' };
  const parts = {};
  for (const chunk of raw.split(`--${boundary}`)) {
    const name = /name="([^"]+)"/.exec(chunk)?.[1];
    if (!name) continue;
    const value = chunk.split(/\r?\n\r?\n/).slice(1).join('\n\n').replace(/\r?\n$/, '');
    parts[name] = /filename="/.test(chunk) ? 'file' : value;
  }
  const out = { kind: 'multipart', parts: Object.keys(parts) };
  if (parts.operations) out.operations = recordedBody(parts.operations).operations || null;
  if (parts.map) { try { out.map = JSON.parse(parts.map); } catch {} }
  return out;
}

function recordedBody(raw, contentType) {
  if (/multipart\/form-data/i.test(contentType || '')) return recordedMultipart(raw, contentType);
  let parsed;
  try { parsed = JSON.parse(raw); } catch { return { kind: 'non-json' }; }
  const bodies = Array.isArray(parsed) ? parsed : [parsed];
  if (bodies.every(body => body && typeof body === 'object' && 'operationName' in body)) {
    return { kind: 'graphql', operations: bodies.map(body => ({
      operation: body.operationName, hash: body.extensions?.persistedQuery?.sha256Hash || null,
      query: typeof body.query === 'string' ? body.query : null, variables: valueShape(body.variables || {}),
    })) };
  }
  return { kind: 'json', shape: valueShape(parsed) };
}

// A write that saves a draft (Mercari: addDraftItemMutation), not the page's
// draft lookups such as existingTemplateDraftItemsQuery.
function draftSaved(entry) {
  return entry.status >= 200 && entry.status < 300 && (entry.body?.operations || [])
    .some(op => /draft/i.test(op.operation || '') && !/query$/i.test(op.operation || ''));
}

async function handleMarketplaceRecord(msg) {
  const marketplace = msg.payload?.marketplace;
  const url = MARKETPLACE_RECORD_START[marketplace];
  if (!url || activeNativeOperation || activeJob || activePatch) {
    sendBrowserResult(msg, { ok: false, error: url ? 'Wait for the current listing action to finish.' : 'Recording is not set up for this marketplace.' });
    return;
  }
  let tab;
  let attached = false;
  const entries = new Map();
  let listener;
  try {
    tab = await openMarketplaceTab(url);
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.debugger.attach({ tabId: tab.id }, '1.3');
    attached = true;
    let finish;
    const finished = new Promise(resolve => { finish = resolve; });
    listener = (source, method, event) => {
      if (source.tabId !== tab.id) return;
      if (method === 'Network.requestWillBeSent' && !['GET', 'HEAD', 'OPTIONS'].includes(event.request.method)) {
        let target;
        try { target = new URL(event.request.url); } catch { return; }
        const entry = { method: event.request.method, url: target.origin + target.pathname,
          header_names: Object.keys(event.request.headers || {}).map(name => name.toLowerCase()).sort(),
          content_type: Object.entries(event.request.headers || {}).find(([name]) => name.toLowerCase() === 'content-type')?.[1] || null };
        entries.set(event.requestId, entry);
        const record = raw => { if (raw) entry.body = recordedBody(raw, entry.content_type); };
        // File uploads arrive as base64 postDataEntries rather than postData.
        const entriesText = (event.request.postDataEntries || []).map(part => part.bytes ? atob(part.bytes) : '').join('');
        if (event.request.postData) record(event.request.postData);
        else if (entriesText) record(entriesText);
        else if (event.request.hasPostData) chrome.debugger.sendCommand({ tabId: tab.id }, 'Network.getRequestPostData', { requestId: event.requestId })
          .then(value => record(value.base64Encoded ? atob(value.postData) : value.postData)).catch(() => {});
      }
      if (method === 'Network.requestWillBeSentExtraInfo' && entries.has(event.requestId)) {
        const entry = entries.get(event.requestId);
        entry.header_names = [...new Set([...entry.header_names, ...Object.keys(event.headers || {}).map(name => name.toLowerCase())])]
          .filter(name => name !== 'cookie').sort();
      }
      if (method === 'Network.responseReceived' && entries.has(event.requestId)) entries.get(event.requestId).status = event.response.status;
      if (method === 'Network.loadingFinished' && entries.has(event.requestId)) {
        const entry = entries.get(event.requestId);
        chrome.debugger.sendCommand({ tabId: tab.id }, 'Network.getResponseBody', { requestId: event.requestId }).then(result => {
          try { entry.response = valueShape(JSON.parse(result.base64Encoded ? atob(result.body) : result.body)); } catch { entry.response = 'non-json'; }
        }).catch(() => {}).finally(() => { if (draftSaved(entry)) setTimeout(finish, 1500); });
      }
    };
    chrome.debugger.onEvent.addListener(listener);
    await chrome.debugger.sendCommand({ tabId: tab.id }, 'Network.enable');
    const closed = new Promise(resolve => {
      const onRemoved = tabId => { if (tabId === tab.id) { chrome.tabs.onRemoved.removeListener(onRemoved); resolve(); } };
      chrome.tabs.onRemoved.addListener(onRemoved);
    });
    await Promise.race([finished, closed, new Promise(resolve => setTimeout(resolve, RECORD_LIMIT_MS))]);
    const requests = [...entries.values()];
    sendBrowserResult(msg, { ok: true, marketplace, draft_saved: requests.some(draftSaved), requests });
  } catch (error) {
    sendBrowserResult(msg, { ok: false, error: `Recording stopped: ${String(error.message || error)}`, requests: [...entries.values()] });
  } finally {
    if (listener) chrome.debugger.onEvent.removeListener(listener);
    if (attached) await chrome.debugger.detach({ tabId: tab.id }).catch(() => {});
  }
}
