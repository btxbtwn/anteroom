// Optional private Vendoo drafts, using the signed-in browser account.
// Photos upload before the item is created. Updates use Firestore version
// preconditions. Publishing and delisting happen in Vendoo's own UI.
// See docs/optional-vendoo-workflow.md.

const VENDOO_MSVC_BASE = 'https://us.vendoo.co';
const VENDOO_FUNCTIONS_BASE = 'https://us-central1-vendoo-prod-7948f.cloudfunctions.net';
const VENDOO_FIRESTORE_BASE = 'https://firestore.googleapis.com/v1/projects/vendoo-prod-7948f/databases/(default)/documents';
const VENDOO_TOKEN_REFRESH_URL = 'https://securetoken.googleapis.com/v1/token';
const VENDOO_APP_URL = 'https://web.vendoo.co/app/';
const VENDOO_TOKEN_MIN_TTL_MS = 5 * 60 * 1000;
const VENDOO_REQUEST_TIMEOUT_MS = 60000;
const VENDOO_PHOTO_CONCURRENCY = 3;
const VENDOO_PHOTO_CACHE_LIMIT = 500;
const VENDOO_PHOTO_CACHE_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const vendooPhotoUploads = new Map();
let vendooPhotoCacheWrite = Promise.resolve();
let vendooSessionRequest = null;

// Firestore auto-ids: 20 chars from this alphabet. Vendoo mints the item id
// client-side with collection.doc().id before calling createItem.
const FIRESTORE_ID_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';

function vendooFirestoreId() {
  const bytes = new Uint8Array(20);
  crypto.getRandomValues(bytes);
  let out = '';
  for (const byte of bytes) out += FIRESTORE_ID_ALPHABET[byte % FIRESTORE_ID_ALPHABET.length];
  return out;
}

// Runs in the page: the only thing we need from web.vendoo.co is the Firebase
// session. Self-contained on purpose (executeScript serializes it).
// Vendoo persists auth in IndexedDB (firebaseLocalStorageDb); older builds also
// used localStorage. Check both so a signed-in tab is never reported as logged out.
async function readVendooSessionInPage() {
  const fromUser = (raw, key) => {
    if (!raw || !raw.uid) return null;
    const sts = raw.stsTokenManager || {};
    return {
      ok: true,
      uid: String(raw.uid),
      email: raw.email || null,
      api_key: raw.apiKey || (key ? String(key).split(':')[2] : null) || null,
      access_token: sts.accessToken || null,
      refresh_token: sts.refreshToken || null,
      expiration_time: Number(sts.expirationTime) || 0,
    };
  };

  const withTimeout = (promise, ms, label) => Promise.race([
    promise,
    new Promise((_, reject) => {
      setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
    }),
  ]);

  // Live Firebase app on the page (fastest, avoids IndexedDB locks).
  // Wait briefly for auth restore — a just-opened /app/ shell often has no
  // currentUser until Firebase finishes reading its persistence layer.
  try {
    const auth = window.firebase?.auth?.();
    if (auth) {
      let user = auth.currentUser;
      if (!user) {
        user = await withTimeout(new Promise((resolve) => {
          const unsub = auth.onAuthStateChanged((u) => {
            unsub();
            resolve(u);
          });
        }), 5000, 'firebase.onAuthStateChanged');
      }
      if (user?.uid) {
        const access_token = await user.getIdToken();
        const refresh_token = user.refreshToken || null;
        return {
          ok: true,
          uid: String(user.uid),
          email: user.email || null,
          api_key: auth.app?.options?.apiKey || null,
          access_token,
          refresh_token,
          expiration_time: Date.now() + 55 * 60 * 1000,
        };
      }
    }
  } catch (err) {
    /* fall through */
  }

  try {
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith('firebase:authUser:')) continue;
      const session = fromUser(JSON.parse(localStorage.getItem(key) || 'null'), key);
      if (session) return session;
    }
  } catch (err) {
    /* fall through to IndexedDB */
  }

  try {
    const db = await withTimeout(new Promise((resolve, reject) => {
      const req = indexedDB.open('firebaseLocalStorageDb');
      req.onerror = () => reject(req.error || new Error('indexedDB open failed'));
      req.onblocked = () => reject(new Error('indexedDB open blocked'));
      req.onsuccess = () => resolve(req.result);
    }), 8000, 'indexedDB.open');
    if (![...db.objectStoreNames].includes('firebaseLocalStorage')) {
      db.close();
      return { ok: false, error: 'Not signed in to Vendoo in this Chrome profile' };
    }
    // Prefer a cursor over getAll — this store can be large enough that getAll hangs.
    const rows = await withTimeout(new Promise((resolve, reject) => {
      const tx = db.transaction('firebaseLocalStorage', 'readonly');
      const store = tx.objectStore('firebaseLocalStorage');
      const req = store.openCursor();
      const found = [];
      req.onerror = () => reject(req.error || new Error('indexedDB cursor failed'));
      req.onsuccess = () => {
        const cursor = req.result;
        if (!cursor) {
          resolve(found);
          return;
        }
        const row = cursor.value;
        const key = String(row?.fbase_key || cursor.key || '');
        if (key.startsWith('firebase:authUser:')) {
          found.push(row);
          resolve(found);
          return;
        }
        cursor.continue();
      };
    }), 8000, 'indexedDB.cursor');
    db.close();
    for (const row of rows) {
      const key = String(row?.fbase_key || '');
      if (!key.startsWith('firebase:authUser:')) continue;
      const value = row?.value;
      const raw = typeof value === 'string' ? JSON.parse(value) : value;
      const session = fromUser(raw, key);
      if (session) return session;
    }
  } catch (err) {
    return { ok: false, error: `Could not read the Vendoo session: ${err.message}` };
  }
  return { ok: false, error: 'Not signed in to Vendoo in this Chrome profile' };
}

async function findOrOpenVendooTab() {
  const tabs = await chrome.tabs.query({ url: ['https://web.vendoo.co/*', 'https://app.vendoo.co/*'] });
  const existing = tabs.find(tab => tab.id);
  if (existing) return existing.id;
  const tab = await chrome.tabs.create({ url: VENDOO_APP_URL, active: true });
  if (!tab.id) throw new Error('Could not open Vendoo sign-in');
  await nativeWait(tab.id, value => value.status === 'complete', Date.now() + 30000);
  return tab.id;
}

async function readVendooSession() {
  const tabId = await findOrOpenVendooTab();
  let lastError = 'No Vendoo session in the page';
  for (let attempt = 0; attempt < 3; attempt++) {
    const [execution] = await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      func: readVendooSessionInPage,
    });
    const session = execution?.result;
    if (session?.ok) return session;
    lastError = session?.error || lastError;
    // Firebase may still be restoring auth from IndexedDB after a hard reload.
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  throw new Error(lastError);
}

async function refreshVendooToken(session) {
  if (!session.refresh_token || !session.api_key) return session;
  const url = `${VENDOO_TOKEN_REFRESH_URL}?key=${encodeURIComponent(session.api_key)}`;
  const headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
  const body = new URLSearchParams({ grant_type: 'refresh_token', refresh_token: session.refresh_token });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), VENDOO_REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, { method: 'POST', signal: controller.signal, headers, body });
    const responseText = await res.text().catch(() => '');
    let data = {};
    try { data = JSON.parse(responseText); } catch (err) { data = {}; }
    if (!res.ok) {
      const error = new Error(`Vendoo session refresh returned ${res.status}`);
      error.invalidSession = ['TOKEN_EXPIRED', 'USER_DISABLED', 'USER_NOT_FOUND', 'INVALID_REFRESH_TOKEN']
        .includes(data.error?.message);
      throw error;
    }
    return {
      ...session,
      access_token: data.id_token,
      refresh_token: data.refresh_token || session.refresh_token,
      expiration_time: Date.now() + Number(data.expires_in || 3600) * 1000,
    };
  } finally {
    clearTimeout(timer);
  }
}

function tokenIsFresh(session) {
  return Boolean(session?.access_token)
    && (session.expiration_time - Date.now()) >= VENDOO_TOKEN_MIN_TTL_MS;
}

// Read the current browser account for every operation batch. An account switch
// in Vendoo must never reuse another account's cached credential.
async function loadFreshVendooSession(rejectedToken = null) {
  let session = await readVendooSession();
  if (!tokenIsFresh(session) || session.access_token === rejectedToken) {
    session = await refreshVendooToken(session);
  }
  if (!session.access_token) throw new Error('Vendoo session has no ID token; sign in to Vendoo again');
  return session;
}

async function freshVendooSession(rejectedToken = null) {
  if (!vendooSessionRequest) {
    vendooSessionRequest = loadFreshVendooSession(rejectedToken).finally(() => { vendooSessionRequest = null; });
  }
  return vendooSessionRequest;
}

// Vendoo HTTP bodies and credentials are never copied to application logs.

async function vendooFetch(url, options = {}) {
  const safe = options.retrySafe === true || ['GET', 'HEAD'].includes(options.method || 'GET');
  const deadline = Date.now() + (options.timeoutMs || VENDOO_REQUEST_TIMEOUT_MS);
  let authRetried = false;
  for (let attempt = 0; ; attempt += 1) {
    let result;
    try {
      result = await vendooFetchOnce(url, { ...options, timeoutMs: Math.max(1, deadline - Date.now()) });
      if (safe && result.status === 401 && options.token && !authRetried && attempt < 2) {
        authRetried = true;
        const session = await freshVendooSession(options.token);
        options = { ...options, token: session.access_token };
        continue;
      }
      if (!safe || ![408, 429, 500, 502, 503, 504].includes(result.status)) return result;
    } catch (err) {
      if (!safe || attempt >= 2 || Date.now() >= deadline) throw err;
    }
    if (attempt >= 2) return result;
    const delay = result?.retryAfterMs ?? Math.floor(Math.random() * (500 * (2 ** attempt)));
    if (delay >= deadline - Date.now()) {
      if (result) return result;
      throw new Error('Vendoo read timed out');
    }
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
}

async function vendooFetchOnce(url, { method = 'GET', token, json, body, headers = {}, responseType = 'json', timeoutMs } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || VENDOO_REQUEST_TIMEOUT_MS);
  const requestHeaders = {
    Accept: 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}),
    ...headers,
  };
  const requestBody = json !== undefined ? JSON.stringify(json) : body;
  try {
    const init = { method, credentials: 'include', signal: controller.signal, headers: requestHeaders };
    if (requestBody !== undefined) init.body = requestBody;
    const res = await fetch(url, init);
    let data = null;
    if (responseType === 'json') {
      const text = await res.text();
      try { data = JSON.parse(text); } catch (err) { data = text; }
    } else if (responseType === 'blob') {
      data = await res.blob();
    }
    const retryAfter = res.headers?.get('Retry-After');
    const retryAfterMs = retryAfter == null ? null : (
      /^\d+(?:\.\d+)?$/.test(retryAfter)
        ? Number(retryAfter) * 1000 : Math.max(0, Date.parse(retryAfter) - Date.now())
    );
    return { ok: res.ok, status: res.status, data, retryAfterMs: Number.isFinite(retryAfterMs) ? retryAfterMs : null };
  } finally {
    clearTimeout(timer);
  }
}

function vendooError(prefix, result) {
  const detail = result?.data && typeof result.data === 'object'
    ? (result.data.error?.message || result.data.message || JSON.stringify(result.data).slice(0, 300))
    : String(result?.data || '').slice(0, 300);
  return `${prefix} returned ${result?.status}${detail ? `: ${detail}` : ''}`;
}

// Photos: ask the inventory service for a signed PUT, send the bytes, and keep
// the storage path. The item then references {version: 3, id: imagePath}.
async function uploadVendooPhoto(session, photo) {
  const extension = String(photo.extension || 'jpg').toLowerCase();
  const source = await nativePhoto(photo.job_id, photo);
  const bytes = new Blob([Uint8Array.from(atob(source.base64), value => value.charCodeAt(0))], {type: source.mime_type});
  const digest = await crypto.subtle.digest('SHA-256', await bytes.arrayBuffer());
  const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const cacheKey = `vendoo_photo_uploads:${session.uid}`;
  const photoKey = `${hash}:${extension}`;
  const flightKey = `${cacheKey}:${photoKey}`;
  const stored = await chrome.storage.local.get(cacheKey);
  const cached = stored?.[cacheKey]?.[photoKey];
  const dimension = Number(photo.max_dimension) || 0;
  if (cached?.image?.id && Date.now() - cached.at < VENDOO_PHOTO_CACHE_TTL_MS) {
    return { ...cached.image, originalMaxDimension: dimension };
  }
  if (!vendooPhotoUploads.has(flightKey)) {
    const upload = uploadVendooPhotoBytes(session, photo, bytes, extension).then(async (image) => {
      // Serialize read/modify/write so concurrent uploads cannot erase one
      // another. Cache only completed PUTs, bounded by count and age.
      const write = vendooPhotoCacheWrite.catch(() => {}).then(async () => {
        const latest = await chrome.storage.local.get(cacheKey);
        const entries = { ...latest?.[cacheKey], [photoKey]: { image, at: Date.now() } };
        const retained = Object.entries(entries)
          .filter(([, entry]) => Date.now() - entry.at < VENDOO_PHOTO_CACHE_TTL_MS)
          .sort((a, b) => b[1].at - a[1].at)
          .slice(0, VENDOO_PHOTO_CACHE_LIMIT);
        await chrome.storage.local.set({ [cacheKey]: Object.fromEntries(retained) });
      });
      vendooPhotoCacheWrite = write;
      await write;
      return image;
    }).finally(() => vendooPhotoUploads.delete(flightKey));
    vendooPhotoUploads.set(flightKey, upload);
  }
  return { ...await vendooPhotoUploads.get(flightKey), originalMaxDimension: dimension };
}

async function uploadVendooPhotoBytes(session, photo, bytes, extension) {
  const slot = await vendooFetch(`${VENDOO_MSVC_BASE}/inventory/v1/images/url`, {
    method: 'POST', token: session.access_token, json: { fileExtension: extension },
  });
  if (!slot.ok || !slot.data?.url || !slot.data?.imagePath) throw new Error(vendooError('Vendoo image slot', slot));
  const put = await vendooFetch(slot.data.url, {
    method: 'PUT',
    body: bytes,
    headers: { 'Content-Type': photo.mime_type || bytes.type || 'image/jpeg' },
    responseType: 'none',
    timeoutMs: 120000,
  });
  if (!put.ok) throw new Error(vendooError('Vendoo image upload', put));
  return {
    version: 3,
    id: slot.data.imagePath,
    originalMaxDimension: Number(photo.max_dimension) || 0,
  };
}

async function readVendooSubscriptionVersion(session) {
  // The form passes the subscription version alongside createItem; it lives on
  // the user's subscription document.
  const doc = await vendooFetch(
    `${VENDOO_FIRESTORE_BASE}/users/${encodeURIComponent(session.uid)}/subscription/details`,
    { token: session.access_token },
  );
  const fields = doc.ok && doc.data && doc.data.fields ? doc.data.fields : null;
  const version = fields && (fields.version?.stringValue || fields.version?.integerValue || fields.current?.mapValue?.fields?.version?.stringValue);
  return version != null ? String(version) : null;
}

async function createVendooItem(session, item, subscriptionVersion) {
  if (item.userID !== session.uid) throw new Error('The Vendoo account changed during Send. Sign in to the original account and retry.');
  const call = await vendooFetch(`${VENDOO_FUNCTIONS_BASE}/items`, {
    method: 'POST',
    token: session.access_token,
    json: { data: { type: 'createItem', payload: { item, subscriptionVersion } } },
    timeoutMs: 90000,
  });
  if (!call.ok || (call.data && call.data.error)) throw new Error(vendooError('Vendoo createItem', call));
  return call.data?.result ?? call.data;
}

async function getVendooItem(session, itemId, { allowMissing = false, withVersion = false } = {}) {
  const doc = await vendooFetch(
    `${VENDOO_FIRESTORE_BASE}/users/${encodeURIComponent(session.uid)}/items/${encodeURIComponent(itemId)}`,
    { token: session.access_token },
  );
  if (allowMissing && doc.status === 404) return null;
  if (doc.status === 404) throw new Error('Vendoo has no draft at this item ID in the signed-in account.');
  if (!doc.ok || !doc.data?.fields || (withVersion && !doc.data.updateTime)) {
    throw new Error(vendooError('Read Vendoo draft', doc));
  }
  const item = firestoreFields(doc.data.fields, true);
  return withVersion ? { ...item, _studio_update_time: doc.data.updateTime } : item;
}

// Firestore's REST shape for one value. Vendoo stores numbers as strings, so
// only the types its documents actually carry are handled.
function firestoreValue(value) {
  if (value === null || value === undefined) return { nullValue: null };
  if (typeof value === 'boolean') return { booleanValue: value };
  if (typeof value === 'number') {
    return Number.isInteger(value) ? { integerValue: String(value) } : { doubleValue: value };
  }
  if (Array.isArray(value)) {
    return { arrayValue: { values: value.map(firestoreValue) } };
  }
  if (typeof value === 'object') {
    // GET /api/item returns Firebase Timestamp instances as their JSON shape.
    // A direct REST update must put them back as Firestore timestamps, not as
    // ordinary {_seconds, _nanoseconds} maps. Vendoo uses these stamps to tell
    // that each marketplace form has been saved.
    const seconds = Number(value._seconds);
    const nanoseconds = Number(value._nanoseconds || 0);
    if (
      Number.isInteger(seconds)
      && Number.isInteger(nanoseconds)
      && nanoseconds >= 0
      && nanoseconds < 1_000_000_000
    ) {
      const wholeSecond = new Date(seconds * 1000).toISOString().replace(/\.\d{3}Z$/, '');
      return { timestampValue: `${wholeSecond}.${String(nanoseconds).padStart(9, '0')}Z` };
    }
    const fields = {};
    for (const [key, inner] of Object.entries(value)) fields[key] = firestoreValue(inner);
    return { mapValue: { fields } };
  }
  return { stringValue: String(value) };
}

// The inverse of ``firestoreValue``: Vendoo's inventory lives in Firestore, so
// one paged listing of users/{uid}/items returns every item document whole and
// there is no need for an /api/item call per listing.
function firestorePlain(value, timestampObjects = false) {
  if (!value || typeof value !== 'object') return null;
  if ('nullValue' in value) return null;
  if ('booleanValue' in value) return Boolean(value.booleanValue);
  if ('integerValue' in value) return Number(value.integerValue);
  if ('doubleValue' in value) return Number(value.doubleValue);
  if ('timestampValue' in value) {
    const timestamp = String(value.timestampValue);
    if (!timestampObjects) return timestamp;
    // Match Firebase Timestamp JSON used by the form payload, retaining all
    // nine fractional digits instead of rounding through JavaScript dates.
    const fraction = timestamp.match(/\.(\d+)/)?.[1] || '';
    return {
      _seconds: Math.floor(Date.parse(timestamp) / 1000),
      _nanoseconds: Number(fraction.padEnd(9, '0')),
    };
  }
  if ('stringValue' in value) return String(value.stringValue);
  if ('bytesValue' in value) return String(value.bytesValue);
  if ('referenceValue' in value) return String(value.referenceValue);
  if ('geoPointValue' in value) return value.geoPointValue;
  if ('arrayValue' in value) return (value.arrayValue?.values || []).map((part) => firestorePlain(part, timestampObjects));
  if ('mapValue' in value) return firestoreFields(value.mapValue?.fields, timestampObjects);
  return null;
}

function firestoreFields(fields, timestampObjects = false) {
  const out = {};
  for (const [key, value] of Object.entries(fields || {})) out[key] = firestorePlain(value, timestampObjects);
  return out;
}


// A field path segment is quoted unless it is a plain identifier — category
// aspect keys like ``53159_Size Type`` carry spaces and start with a digit.
function firestorePathSegment(segment) {
  return /^[A-Za-z_][A-Za-z_0-9]*$/.test(segment) ? segment : `\`${segment.replace(/`/g, '\\`')}\``;
}

function firestoreFieldPath(path) {
  return String(path).split('.').map(firestorePathSegment).join('.');
}

// Edit an item the way Vendoo's own form does: a Firestore update of just the
// fields that changed, so nothing else in the document is disturbed.
async function updateVendooItem(session, call) {
  const updates = call.updates && typeof call.updates === 'object' ? call.updates : {};
  const paths = Object.keys(updates);
  if (!paths.length) return { updated: [] };

  // Firestore wants the value nested under the path's first segment, with the
  // full path in the mask.
  const fields = {};
  for (const [path, value] of Object.entries(updates)) {
    const [head, ...rest] = String(path).split('.');
    if (!rest.length) {
      fields[head] = firestoreValue(value);
      continue;
    }
    let node = fields[head] || (fields[head] = { mapValue: { fields: {} } });
    for (let i = 0; i < rest.length - 1; i += 1) {
      const part = rest[i];
      const parent = node.mapValue.fields;
      node = parent[part] || (parent[part] = { mapValue: { fields: {} } });
    }
    node.mapValue.fields[rest[rest.length - 1]] = firestoreValue(value);
  }

  const params = new URLSearchParams();
  for (const path of paths) params.append('updateMask.fieldPaths', firestoreFieldPath(path));
  if (call.expected_update_time) params.set('currentDocument.updateTime', call.expected_update_time);
  const url = `${VENDOO_FIRESTORE_BASE}/users/${encodeURIComponent(session.uid)}`
    + `/items/${encodeURIComponent(call.item_id)}?${params}`;
  const res = await vendooFetch(url, {
    method: 'PATCH',
    token: session.access_token,
    json: { fields },
  });
  if ([409, 412].includes(res.status) || res.data?.error?.status === 'FAILED_PRECONDITION') {
    throw new Error('The Vendoo draft changed during this save. Refresh it and review your changes before trying again.');
  }
  if (!res.ok) throw new Error(vendooError(`update ${call.item_id}`, res));
  return { updated: paths, update_time: res.data?.updateTime || null };
}

// An item's ``labels`` are ids into users/{uid}/labels, not names — the form
// shows only chips whose id it finds there. Match names the way Vendoo does
// (trimmed, case-insensitive) and create any that are missing, as picking
// "Create" in the label box would. An id passed in (a pulled item) is kept.
const VENDOO_LABEL_COLOR = '#4852e8';

async function listVendooLabels(session) {
  const base = `${VENDOO_FIRESTORE_BASE}/users/${encodeURIComponent(session.uid)}/labels`;
  const labels = [];
  let pageToken = '';
  do {
    const params = new URLSearchParams({ pageSize: '300' });
    if (pageToken) params.set('pageToken', pageToken);
    const res = await vendooFetch(`${base}?${params}`, { token: session.access_token });
    if (!res.ok) throw new Error(vendooError('list labels', res));
    for (const doc of res.data?.documents || []) {
      const id = String(doc.name || '').split('/').pop();
      const name = doc.fields?.name?.stringValue || '';
      if (id) labels.push({ id, name });
    }
    pageToken = res.data?.nextPageToken || '';
  } while (pageToken);
  return labels;
}

async function resolveVendooLabels(session, call) {
  const names = Array.isArray(call.names) ? call.names : [];
  const existing = await listVendooLabels(session);
  const byName = new Map(existing.map((label) => [label.name.trim().toLowerCase(), label.id]));
  const ids = new Set(existing.map((label) => label.id));
  const out = [];
  const created = [];
  for (const raw of names) {
    const name = String(raw || '').trim();
    if (!name) continue;
    let id = ids.has(name) ? name : byName.get(name.toLowerCase());
    if (!id) {
      id = vendooFirestoreId();
      const url = `${VENDOO_FIRESTORE_BASE}/users/${encodeURIComponent(session.uid)}/labels/${id}`;
      const res = await vendooFetch(url, {
        method: 'PATCH',
        token: session.access_token,
        json: {
          fields: {
            id: { stringValue: id },
            name: { stringValue: name },
            color: { stringValue: VENDOO_LABEL_COLOR },
            createdAt: { timestampValue: new Date().toISOString() },
          },
        },
      });
      if (!res.ok) throw new Error(vendooError(`create label ${name}`, res));
      byName.set(name.toLowerCase(), id);
      ids.add(id);
      created.push(name);
    }
    if (!out.includes(id)) out.push(id);
  }
  return { ids: out, created };
}

const VENDOO_READ_OPS = new Set(['get_item', 'subscription']);
const VENDOO_READ_CONCURRENCY = 3;
const VENDOO_DRAFT_OPS = new Set([...VENDOO_READ_OPS, 'session', 'new_item_id', 'upload_photo',
  'create_item', 'resolve_labels', 'update_item']);

async function runVendooRead(session, op) {
  if (op.op === 'subscription') return { version: await readVendooSubscriptionVersion(session) };
  return { item_id: op.item_id, item: await getVendooItem(session, op.item_id, {
    allowMissing: op.allow_missing === true, withVersion: op.with_version === true,
  }) };
}

// One Studio message, a list of ops, one reply. Studio sequences the calls it
// needs (probe, upload, create, verify). Independent photo uploads run in small
// batches, in result order; every batch settles before any dependent write.
// A failed upload stops the sequence before the draft can be created.
async function runVendooApiOps(ops, onProgress = () => {}) {
  if (!Array.isArray(ops) || !ops.length || ops.some(op => !VENDOO_DRAFT_OPS.has(op?.op))) {
    return { ok: false, error: 'Only private Vendoo draft operations are supported.', results: [] };
  }
  const session = await freshVendooSession();
  const results = [];
  const totalPhotos = ops.filter((op) => op.op === 'upload_photo').length;
  let completedPhotos = 0;
  if (totalPhotos) onProgress({ completed: 0, total: totalPhotos });
  for (let index = 0; index < ops.length; index += 1) {
    const op = ops[index];
    if (VENDOO_READ_OPS.has(op.op)) {
      const batch = [op];
      while (!op.throttle_ms && batch.length < VENDOO_READ_CONCURRENCY
        && VENDOO_READ_OPS.has(ops[index + batch.length]?.op)
        && !ops[index + batch.length].throttle_ms) {
        batch.push(ops[index + batch.length]);
      }
      const reads = await Promise.all(batch.map(async (call) => {
        try {
          return { op: call.op, ok: true, ...await runVendooRead(session, call) };
        } catch (err) {
          return { op: call.op, ok: false, error: String(err.message || err) };
        }
      }));
      results.push(...reads);
      if (reads.some((result) => !result.ok)) return { ok: false, uid: session.uid, results };
      index += batch.length - 1;
      if (op.throttle_ms) await new Promise((resolve) => setTimeout(resolve, op.throttle_ms));
      continue;
    }
    if (op.op === 'upload_photo' && !op.throttle_ms) {
      const batch = [];
      while (batch.length < VENDOO_PHOTO_CONCURRENCY
        && ops[index + batch.length]?.op === 'upload_photo'
        && !ops[index + batch.length].throttle_ms) {
        batch.push(ops[index + batch.length]);
      }
      const uploaded = await Promise.all(batch.map(async (call) => {
        try {
          const image = await uploadVendooPhoto(session, call.photo || {});
          completedPhotos += 1;
          onProgress({ completed: completedPhotos, total: totalPhotos });
          return {
            op: 'upload_photo', ok: true, photo_id: call.photo?.id || null,
            image,
          };
        } catch (err) {
          return {
            op: 'upload_photo', ok: false, photo_id: call.photo?.id || null,
            error: String(err && err.message ? err.message : err),
          };
        }
      }));
      results.push(...uploaded);
      if (uploaded.some((result) => !result.ok)) return { ok: false, uid: session.uid, results };
      index += batch.length - 1;
      continue;
    }
    try {
      switch (op.op) {
        case 'session':
          if (op.expected_uid && op.expected_uid !== session.uid) {
            throw new Error('This interrupted send belongs to a different Vendoo account. Sign in to that account to recover it.');
          }
          results.push({ op: 'session', ok: true, uid: session.uid, email: session.email });
          break;
        case 'new_item_id':
          results.push({ op: 'new_item_id', ok: true, item_id: vendooFirestoreId() });
          break;
        case 'upload_photo':
          results.push({ op: 'upload_photo', ok: true, photo_id: op.photo?.id || null, image: await uploadVendooPhoto(session, op.photo || {}) });
          completedPhotos += 1;
          onProgress({ completed: completedPhotos, total: totalPhotos });
          break;
        case 'create_item':
          results.push({ op: 'create_item', ok: true, result: await createVendooItem(session, op.item, op.subscription_version ?? null) });
          break;
        case 'resolve_labels':
          results.push({ op: 'resolve_labels', ok: true, ...(await resolveVendooLabels(session, op)) });
          break;
        case 'update_item':
          if (typeof op.expected_update_time !== 'string' || !op.expected_update_time) {
            throw new Error('Read the current Vendoo draft version before saving changes.');
          }
          results.push({ op: 'update_item', ok: true, item_id: op.item_id, ...(await updateVendooItem(session, op)) });
          break;
        default:
          throw new Error(`Unknown Vendoo API op: ${op.op}`);
      }
    } catch (err) {
      results.push({ op: op.op, ok: false, error: String(err && err.message ? err.message : err) });
      return { ok: false, uid: session.uid, results };
    }
    if (op.throttle_ms) await new Promise((resolve) => setTimeout(resolve, op.throttle_ms));
  }
  return { ok: true, uid: session.uid, results };
}

function replyVendooApi(msg, payload) {
  const request = msg.payload || {};
  send({
    version: 1,
    type: 'browser.result',
    job_id: msg.job_id || request.job_id || null,
    message_id: request.request_id || Date.now().toString(36),
    sent_at: new Date().toISOString(),
    payload: { ...payload, request_id: request.request_id || null },
  });
}

async function handleVendooApiMessage(msg, reply = result => replyVendooApi(msg, result)) {
  const payload = msg.payload || {};
  const ops = Array.isArray(payload.ops) ? payload.ops.map(op => op.op === 'upload_photo' ? {...op, photo: {...op.photo, job_id: msg.job_id}} : op) : [];
  if (payload.listing_mode !== 'vendoo') {
    reply( {ok: false, error: 'Vendoo mode is not enabled.', results: []}); return;
  }
  if (!ops.length) {
    reply( { ok: false, error: 'job.vendoo_api needs ops', results: [] });
    return;
  }
  try {
    reply(await runVendooApiOps(ops));
  } catch {
    reply({ ok: false, error: 'Vendoo did not finish this request. Check the draft before trying again.', results: [] });
  }
}
