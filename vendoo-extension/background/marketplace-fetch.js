// Every direct marketplace request goes through marketplaceFetch: one place for
// timeouts bounded by the operation deadline, cancellation, retries of safe
// requests, and turning a marketplace's answer into the result contract's
// error codes. Creates and publishes are sent once and never retried here.
const MARKETPLACE_LABELS = { poshmark: 'Poshmark', mercari: 'Mercari', depop: 'Depop', sellwild: 'Sellwild', vinted: 'Vinted', facebook: 'Facebook' };
const MARKETPLACE_RETRY_DELAYS_MS = [400, 1200, 3000];
const MARKETPLACE_MAX_RETRY_AFTER_S = 20;
// Cloudflare, PerimeterX, DataDome and similar bot checks.
const MARKETPLACE_CHALLENGE = /challenge-platform|challenge-error-text|Just a moment\.\.\.|cf-chl-|cf_chl_|captcha|datadome|px-captcha|perimeterx|_pxhd|verify (?:that )?you(?: are|'re) (?:a )?human/i;
const MARKETPLACE_LOGIN_PAGE = /type=["']?password|\/(?:login|signin|sign-in|sign_in)\b/i;

async function marketplaceSleep(ms) {
  await new Promise(resolve => setTimeout(resolve, ms));
}

function marketplaceLabel(marketplace) {
  return MARKETPLACE_LABELS[marketplace] || 'The marketplace';
}

// Structured per-step log. Never cookies, tokens, URLs with queries or listing text.
function marketplaceLog(marketplace, step, details = {}) {
  const fields = { op: nativeOperationFor(marketplace)?.id || null, step, ...details };
  const text = Object.entries(fields).filter(([, value]) => value != null && value !== '')
    .map(([key, value]) => `${key}=${value}`).join(' ');
  console.log(`[${String(marketplace || 'market').toUpperCase()}] ${text}`);
}

// The marketplace's own words, without anything that looks like a secret.
function marketplaceScrub(text) {
  return String(text ?? '')
    .replace(/[\u0000-\u001f\u007f]+/g, ' ')
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '[email]')
    .replace(/\bBearer\s+\S+/gi, 'Bearer [hidden]')
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?/g, '[token]')
    .replace(/\b([\w-]*(?:token|session|cookie|csrf|secret|key|auth)[\w-]*)\s*[=:]\s*["']?[^\s"';,]+/gi, '$1=[hidden]')
    .replace(/("(?:[^"\\]|\\)*(?:token|session|cookie|csrf|secret|key|auth)(?:[^"\\]|\\)*"\s*:\s*")(?:[^"\\]|\\)*(")/gi, '$1[hidden]$2')
    .replace(/\b[A-Za-z0-9+/_-]{32,}={0,2}/g, '[hidden]')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 4000);
}

// Message-like fields of a marketplace error body, never the whole body.
function marketplaceBodyMessage(body) {
  if (body == null) return '';
  if (typeof body === 'string') return body;
  if (typeof body !== 'object') return '';
  const parts = [];
  const add = value => { if (typeof value === 'string' && value.trim()) parts.push(value.trim()); };
  const error = body.error;
  if (typeof error === 'string') add(error);
  else if (error && typeof error === 'object') {
    add(error.userMessage); if (!error.userMessage) add(error.message);
    if (error.errorType && !parts.length) add(error.errorType);
  }
  if (Array.isArray(body.errors)) {
    for (const entry of body.errors.slice(0, 5)) {
      if (typeof entry === 'string') add(entry);
      else if (entry && typeof entry === 'object') {
        const field = entry.field || entry.param || entry.extensions?.field;
        add(field && entry.message ? `${field}: ${entry.message}` : entry.message);
      }
    }
  } else if (body.errors && typeof body.errors === 'object') {
    for (const [field, value] of Object.entries(body.errors).slice(0, 5)) add(`${field}: ${Array.isArray(value) ? value.join(', ') : value}`);
  }
  add(body.message); add(body.error_description); add(body.detail);
  return [...new Set(parts)].join(' ');
}

// An error carrying the contract fields. `rejected` is true only when nothing
// was written: the request was never sent, or the marketplace refused it.
function marketplaceError(message, { code = 'rejected', rejected = true, status = null, marketplace_message = '',
  retry_after_s = null, fields = null } = {}) {
  const scrubbed = marketplaceScrub(marketplace_message);
  return Object.assign(new Error(message), { code, rejected, status,
    ...(scrubbed ? { marketplace_message: scrubbed } : {}),
    ...(retry_after_s != null ? { retry_after_s } : {}),
    ...(fields?.length ? { fields } : {}) });
}

// Fields the marketplace (or Anteroom's pre-flight) says are missing. Each
// entry is { field: the marketplace's own name, needs: a plain noun phrase }.
function marketplaceMissing(marketplace, entries, marketplace_message = '') {
  const label = marketplaceLabel(marketplace);
  const needs = [...new Set(entries.map(entry => entry.needs))];
  return marketplaceError(`${label} needs ${needs.join(', ')}.`, { code: 'missing_fields', marketplace_message,
    fields: entries.map(entry => ({ field: entry.field, message: `${label} needs ${entry.needs}` })) });
}

// A failed reply in the shared contract. Unsafe always means uncertain.
function marketplaceFailure(error, safe, extra = {}) {
  const out = { ok: false, ...extra, safe_to_retry: safe === true, error: String(error?.message || error),
    error_code: safe === true ? (error?.code && error.code !== 'uncertain' ? error.code : 'rejected') : 'uncertain' };
  if (error?.marketplace_message) out.marketplace_message = error.marketplace_message;
  if (error?.retry_after_s != null) out.retry_after_s = error.retry_after_s;
  if (error?.fields?.length) out.fields = error.fields;
  return out;
}

// Same text, ignoring line endings and runs of spaces the marketplace rewrites.
function marketplaceText(value) {
  return String(value ?? '').replace(/\r\n?/g, '\n').replace(/[ \t ]+/g, ' ').replace(/ *\n */g, '\n').trim();
}

// The marketplace's own title, description, or brand when the seller set one.
// A stored empty string stays empty. A missing key still uses General.
function marketplaceCopy(listing, marketplace, field) {
  const block = listing?.[`${marketplace}_specifics`];
  if (block && typeof block === 'object' && !Array.isArray(block)) {
    if (Object.hasOwn(block, field)) return block[field];
    const want = field.toLowerCase();
    const key = Object.keys(block).find(name => name.toLowerCase() === want);
    if (key) return block[key];
  }
  return listing?.[field];
}

function marketplaceRetryAfter(response) {
  const raw = response.headers?.get?.('retry-after');
  if (!raw) return null;
  const seconds = /^\d+$/.test(raw.trim()) ? Number(raw) : Math.ceil((Date.parse(raw) - Date.now()) / 1000);
  return Number.isFinite(seconds) ? Math.max(0, seconds) : null;
}

// A presigned storage URL refuses with 403 when its signature is stale; that
// is a refused upload, not a signed-out seller.
function marketplaceStorageRefusal(marketplace, error) {
  if (error?.code !== 'signed_out') return error;
  return marketplaceError(`${marketplaceLabel(marketplace)}'s photo storage refused a photo upload.`,
    { status: error.status, marketplace_message: error.marketplace_message });
}

function marketplaceStopped(marketplace) {
  return marketplaceError(`The ${marketplaceLabel(marketplace)} action stopped before this step.`, { code: 'network' });
}

// One request. Options:
//   marketplace, step: for messages and logs
//   method, headers, body, credentials
//   deadline: the operation's deadline (ms epoch); every attempt ends by it
//   timeoutMs: per-attempt cap
//   retry: true only for requests that write nothing or are idempotent
//   expect: 'json' (default), 'text' or 'none'
//   guard: false for cleanup that must run after a cancel or the deadline
// Returns { status, data, headers }.
async function marketplaceFetch(url, { marketplace, step, method = 'GET', headers = {}, body, credentials = 'include',
  deadline, timeoutMs = 25000, retry = method === 'GET', expect = 'json', guard = true, cache } = {}) {
  const label = marketplaceLabel(marketplace);
  const write = !['GET', 'HEAD'].includes(method);
  await ensureMarketplaceRequestContext();
  const operation = guard ? nativeOperationFor(marketplace) : null;
  for (let attempt = 0; ; attempt += 1) {
    if (guard && (operation?.cancelled || Date.now() >= deadline)) throw marketplaceStopped(marketplace);
    const remaining = deadline - Date.now();
    const signals = [AbortSignal.timeout(Math.max(500, Math.min(timeoutMs, remaining)))];
    if (operation?.controller) signals.push(operation.controller.signal);
    const started = Date.now();
    let response;
    let failure = null;
    try {
      response = await fetch(url, { method, headers, credentials, ...(body === undefined ? {} : { body }),
        ...(cache ? { cache } : {}),
        signal: signals.length > 1 ? AbortSignal.any(signals) : signals[0] });
    } catch (error) {
      const cancelled = operation?.cancelled === true;
      failure = marketplaceError(cancelled ? `The ${label} action was cancelled.`
        : `${label} did not answer${error?.name === 'TimeoutError' ? ' in time' : ''}.`,
      { code: write ? 'uncertain' : 'network', rejected: false });
      if (cancelled) failure.cancelled = true;
    }
    if (response) {
      const ms = Date.now() - started;
      let text = '';
      if (expect !== 'none' || !response.ok) {
        try { text = await response.text(); } catch { text = ''; }
      }
      const json = /json/i.test(response.headers?.get?.('content-type') || '');
      let data = null;
      if (json && text) { try { data = JSON.parse(text); } catch { data = null; } }
      const status = response.status;
      const statusLine = `HTTP ${status}${response.statusText ? ` ${response.statusText}` : ''}`;
      const plain = !data && text && !/<html|<!doctype/i.test(text) ? marketplaceScrub(text).slice(0, 180) : '';
      const ownWords = data ? marketplaceBodyMessage(data) || statusLine : (plain || statusLine);
      // Normal pages carry bot-check scripts too, so only a blocked or
      // unexpected answer is read for challenge markers.
      const unexpected = status < 300 && status !== 204 && expect === 'json' && !data;
      const challenged = ([401, 403, 429, 503].includes(status) || unexpected) && MARKETPLACE_CHALLENGE.test(json ? ownWords : text);
      if (challenged) {
        failure = marketplaceError(`${label} asked for a browser check. Open ${label} in Chrome, finish the check, then try again.`,
          { code: 'challenge', status, marketplace_message: ownWords });
      } else if (status === 401 || status === 403) {
        failure = marketplaceError(`${label} asked to sign in again. Reconnect ${label} in Anteroom.`,
          { code: 'signed_out', status, marketplace_message: ownWords });
      } else if (status === 429) {
        const wait = marketplaceRetryAfter(response);
        failure = marketplaceError(`${label} is limiting requests right now. Wait${wait ? ` ${wait} seconds` : ' a few minutes'}, then try again.`,
          { code: 'rate_limited', status, marketplace_message: ownWords, retry_after_s: wait });
        failure.wait_s = wait;
      } else if (status >= 500 || status === 408) {
        failure = marketplaceError(`${label} had a problem answering (HTTP ${status}).`,
          { code: write ? 'uncertain' : 'network', rejected: false, status, marketplace_message: ownWords });
      } else if (status >= 400) {
        // 409 can mean the write already happened.
        failure = marketplaceError(`${label} refused the request (HTTP ${status}).`,
          { code: 'rejected', rejected: status !== 409, status, marketplace_message: ownWords });
      } else if (unexpected) {
        const login = response.redirected && MARKETPLACE_LOGIN_PAGE.test(new URL(response.url || url).pathname)
          || (!json && MARKETPLACE_LOGIN_PAGE.test(text.slice(0, 200000)) && /<html/i.test(text));
        failure = login
          ? marketplaceError(`${label} asked to sign in again. Reconnect ${label} in Anteroom.`, { code: 'signed_out', status, marketplace_message: statusLine })
          : marketplaceError(`${label} answered in a way Anteroom does not recognise.`, { code: 'schema_drift', rejected: !write, status, marketplace_message: statusLine });
      }
      // The marketplace's own body, not only the one message field it picked.
      // Callers surface this on the job so a refusal names the field.
      // A plain-text refusal (Depop's presentation layer answers its 400s this
      // way) is kept too, unless it is an HTML page.
      if (failure && data && typeof data === 'object') {
        failure.response_body = marketplaceScrub(JSON.stringify(data));
        // The parsed body, for a caller that hides more than tokens. Never exported as is.
        Object.defineProperty(failure, 'response_data', { value: data, enumerable: false });
      } else if (failure && text && !/<html|<!doctype/i.test(text)) {
        failure.response_body = marketplaceScrub(text);
      }
      marketplaceLog(marketplace, step, { method, status, attempt: attempt || null, error_code: failure?.code, ms });
      if (!failure) return { status, data: expect === 'text' ? text : (data ?? {}), headers: response.headers };
    } else {
      marketplaceLog(marketplace, step, { method, status: 'no-reply', attempt: attempt || null, error_code: failure.code, ms: Date.now() - started });
    }
    const transient = failure.cancelled !== true && ['network', 'uncertain', 'rate_limited'].includes(failure.code);
    const wait = failure.wait_s != null ? failure.wait_s * 1000
      : MARKETPLACE_RETRY_DELAYS_MS[attempt] * (0.75 + Math.random() * 0.5);
    if (!retry || !transient || attempt >= MARKETPLACE_RETRY_DELAYS_MS.length
        || (failure.wait_s ?? 0) > MARKETPLACE_MAX_RETRY_AFTER_S || Date.now() + wait + 1000 >= deadline) {
      // A retried read that still fails wrote nothing.
      if (retry && failure.code === 'uncertain') Object.assign(failure, { code: 'network', rejected: true });
      delete failure.wait_s;
      throw failure;
    }
    await marketplaceSleep(wait);
  }
}
