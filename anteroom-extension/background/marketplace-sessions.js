const MARKETPLACE_SIGN_IN = {
  depop: 'https://www.depop.com/sellinghub/drafts/',
  poshmark: 'https://poshmark.com/feed',
  mercari: 'https://www.mercari.com/mypage/',
};
const EXTENSION_SESSION_MARKETS = ['depop', 'poshmark', 'mercari'];
const SESSION_EXPIRING_MS = 24 * 60 * 60 * 1000;

// Session status checks return only this summary. Cookies stay in Chrome unless
// the seller explicitly approves connecting Poshmark in Anteroom Cloud.
function sessionLabel(record) {
  if (!record || typeof record !== 'object') return null;
  for (const key of ['username', 'user_name', 'name', 'handle', 'email']) {
    const value = record[key];
    if (typeof value !== 'string') continue;
    const label = value.trim();
    // oxlint-disable-next-line no-control-regex -- rejects labels with control characters
    if (label && label.length <= 80 && !/[\u0000-\u001f]/.test(label)) return label;
  }
  return null;
}

function jwtClaims(token) {
  try {
    const part = String(token || '').split('.')[1];
    if (!part || typeof atob !== 'function') return null;
    const padded = part.replace(/-/g, '+').replace(/_/g, '/');
    const claims = JSON.parse(atob(padded + '='.repeat((4 - (padded.length % 4)) % 4)));
    return claims && typeof claims === 'object' ? claims : null;
  } catch {
    return null;
  }
}

function cookieToken(cookie) {
  if (!cookie?.value) return '';
  try { return decodeURIComponent(cookie.value); } catch { return cookie.value; }
}

function soonestExpiry(cookie, token) {
  const times = [];
  if (cookie && !cookie.session && Number.isFinite(cookie.expirationDate)) times.push(Math.floor(cookie.expirationDate));
  const exp = jwtClaims(token)?.exp;
  if (Number.isFinite(exp)) times.push(Math.floor(exp));
  return times.length ? Math.min(...times) : null;
}

function publicExtensionSession(present, expiresAt, label, now = Date.now()) {
  const expires_at = Number.isFinite(expiresAt) ? Math.floor(expiresAt) : null;
  const state = !present ? 'signed_out' : expires_at != null && expires_at * 1000 <= now ? 'expired' : 'signed_in';
  return {
    state,
    expires_at: state === 'signed_out' ? null : expires_at,
    label: state === 'signed_out' ? null : sessionLabel(label),
    expiring: state === 'signed_in' && expires_at != null && expires_at * 1000 - now <= SESSION_EXPIRING_MS,
  };
}

function unknownExtensionSession() {
  return { state: 'unknown', expires_at: null, label: null, expiring: false };
}

async function cookieSession(url, name) {
  const cookie = await chrome.cookies.get({ url, name });
  const token = cookieToken(cookie);
  return publicExtensionSession(Boolean(token), soonestExpiry(cookie, token), jwtClaims(token));
}

// Mercari's own session start answers with the signed-in user, if any.
async function readMercariSession(now = Date.now()) {
  let session;
  try {
    session = await mercariInitialize(Date.now() + 10000, 'session');
  } catch (error) {
    return error.code === 'signed_out' ? publicExtensionSession(false, null, null, now) : unknownExtensionSession();
  }
  return publicExtensionSession(true, soonestExpiry(null, session.accessToken), { name: session.user.name }, now);
}


async function readExtensionSession(marketplace, now = Date.now(), region) {
  try {
    if (marketplace === 'depop') return await cookieSession('https://www.depop.com/', 'access_token');
    if (marketplace === 'poshmark') return await cookieSession(poshmarkOrigin(region) + '/', 'jwt');
    if (marketplace === 'mercari') return await readMercariSession(now);
  } catch { /* one marketplace failing must not blank the others */ }
  return unknownExtensionSession();
}

async function readExtensionSessions(now = Date.now(), region) {
  const entries = await Promise.all(EXTENSION_SESSION_MARKETS.map(async marketplace => [marketplace, await readExtensionSession(marketplace, now, region)]));
  return Object.fromEntries(entries);
}

async function handleMarketplaceSessions(msg) {
  try {
    sendBrowserResult(msg, { ok: true, sessions: await readExtensionSessions(undefined, msg.payload?.seller_region) });
  } catch {
    sendBrowserResult(msg, { ok: false, error: 'Could not check marketplace sign-in.' });
  }
}

async function checkDepopSignIn() {
  const signal = AbortSignal.timeout(8000);
  const context = ensureMarketplaceRequestContext();
  let onAbort;
  const aborted = new Promise((_, reject) => {
    onAbort = () => reject(signal.reason);
    signal.addEventListener('abort', onAbort, { once: true });
  });
  try {
    return await Promise.race([aborted, (async () => {
      await context;
      signal.throwIfAborted();
      const authorization = await depopBearer();
      signal.throwIfAborted();
      const response = await fetch('https://webapi.depop.com/presentation/api/v1/drafts/?limit=24&sort_by=createdAt', {
        headers: { Accept: 'application/json', Authorization: authorization }, credentials: 'omit', signal,
      });
      void response.body?.cancel().catch(() => {});
      return response;
    })()]);
  } catch (error) {
    if (signal.aborted && marketplaceNetworkReady === context) marketplaceNetworkReady = null;
    throw error;
  } finally {
    signal.removeEventListener('abort', onAbort);
  }
}

async function handleMarketplaceConnect(msg, reply = result => sendBrowserResult(msg, result)) {
  const marketplace = msg.payload?.marketplace;
  const region = msg.payload?.seller_region;
  const url = marketplace === 'poshmark' && region === 'CA' ? poshmarkOrigin(region) + '/feed' : MARKETPLACE_SIGN_IN[marketplace];
  if (!url || nativeOperationsRunning()) {
    reply({ ok: false, error: url ? 'Wait for the current listing action to finish.' : 'Unsupported marketplace.' });
    return;
  }
  try {
    const origin = new URL(url).origin;
    const existing = (await chrome.tabs.query({ url: `${origin}/*` })).findLast(value => new URL(value.url).pathname === new URL(url).pathname);
    const tab = existing || await openMarketplaceTab(url);
    await chrome.tabs.update(tab.id, { active: true });
    await nativeWait(tab.id, value => value.status === 'complete' && String(value.url || '').startsWith(`${origin}/`), Date.now() + 30000);
    let summary = await readExtensionSession(marketplace, undefined, region);
    if (marketplace === 'depop' && summary.state === 'signed_in') {
      let response;
      try {
        response = await checkDepopSignIn();
      } catch {
        reply({ ok: false, error: 'Depop did not answer the sign-in check. Open Depop in Chrome, then try again.' });
        return;
      }
      if (response.status === 401 || response.status === 403) summary = { ...summary, state: 'expired', expiring: false };
      else if (!response.ok) summary = unknownExtensionSession();
    }
    reply({ ok: true, signed_in: summary.state === 'signed_in', ...summary });
  } catch { reply({ ok: false, error: 'Finish signing in in Chrome, then reconnect. Listing actions will not open tabs.' }); }
}
