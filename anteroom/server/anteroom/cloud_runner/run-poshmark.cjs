'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const readline = require('node:readline');
const crypto = require('node:crypto');
const { Cookie, CookieJar } = require('tough-cookie');

const POSHMARK_HEADER_PATH = /^\/(?:vm-rest\/|api\/|create-listing$)/;
const ALLOWED_S3_HOST = /^[a-z0-9][a-z0-9.-]*\.s3(?:[-.][a-z0-9-]+)?\.amazonaws\.com$/i;
const ALLOWED_CLOUDFRONT_HOST = /^[a-z0-9][a-z0-9.-]*\.cloudfront\.net$/i;
const DEFAULT_USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const DURABLE_STORAGE_KEYS = new Set(['poshmark_last_share_at', 'orderHistoryFullWalks']);
const MAX_REDIRECTS = 5;

const EXTENSION_SCRIPTS = [
  'marketplace-fetch.js',
  'marketplace-catalog.js',
  'poshmark-native.js',
  'native-marketplaces.js',
  'marketplace-delete.js',
  'marketplace-promotions.js',
  'marketplace-likes.js',
  'marketplace-sessions.js',
  'marketplace-sales.js',
  'marketplace-order-history.js',
  'marketplace-import.js',
  'native-forms.js',
  'cloud-marketplace-actions.js',
];

function isPoshmarkCookieHost(hostname) {
  const host = String(hostname || '').toLowerCase();
  return host === 'poshmark.com' || host.endsWith('.poshmark.com');
}

function isAllowedHost(hostname) {
  const host = String(hostname || '').toLowerCase();
  if (isPoshmarkCookieHost(host)) return true;
  if (ALLOWED_S3_HOST.test(host)) return true;
  if (ALLOWED_CLOUDFRONT_HOST.test(host)) return true;
  return false;
}

function assertAllowedUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(String(rawUrl));
  } catch {
    throw new Error('Invalid marketplace URL.');
  }
  if (parsed.protocol !== 'https:') {
    throw new Error(`Blocked non-HTTPS URL protocol: ${parsed.protocol}`);
  }
  if (parsed.username || parsed.password) {
    throw new Error('Blocked URL with embedded credentials.');
  }
  if (parsed.port && parsed.port !== '443') {
    throw new Error(`Blocked non-standard port: ${parsed.port}`);
  }
  if (!isAllowedHost(parsed.hostname)) {
    throw new Error(`Blocked outbound host: ${parsed.hostname}`);
  }
  return parsed;
}

function isDurableKey(key) {
  return (
    key.startsWith('native_operation_') ||
    key.startsWith('promotion_') ||
    DURABLE_STORAGE_KEYS.has(key)
  );
}

function buildCookieJar(cookies) {
  const jar = new CookieJar(undefined, { looseMode: true, allowSpecialUseDomain: false });
  if (!Array.isArray(cookies)) return jar;
  for (const raw of cookies) {
    if (!raw || typeof raw !== 'object' || !raw.name) continue;
    const rawDomain = String(raw.domain || 'poshmark.com').toLowerCase();
    const cleanDomain = rawDomain.replace(/^\./, '');
    if (!isPoshmarkCookieHost(cleanDomain)) continue;
    const hostOnly = !rawDomain.startsWith('.');
    const expires =
      raw.session || !Number.isFinite(Number(raw.expirationDate))
        ? 'Infinity'
        : new Date(Number(raw.expirationDate) * 1000);
    const cookie = new Cookie({
      key: String(raw.name),
      value: String(raw.value ?? ''),
      domain: cleanDomain,
      path: String(raw.path || '/'),
      secure: Boolean(raw.secure),
      httpOnly: Boolean(raw.httpOnly),
      hostOnly,
      expires,
      sameSite: typeof raw.sameSite === 'string' ? raw.sameSite : undefined,
    });
    const origin = `https://${cleanDomain}${cookie.path || '/'}`;
    try {
      jar.setCookieSync(cookie, origin, { ignoreError: true });
    } catch {
      // Ignore malformed cookies from external capture
    }
  }
  return jar;
}

function exportCookies(jar) {
  const serialized = jar.serializeSync();
  const out = [];
  for (const entry of serialized.cookies || []) {
    const cleanDomain = String(entry.domain || '').toLowerCase().replace(/^\./, '');
    if (!isPoshmarkCookieHost(cleanDomain)) continue;
    const domain = entry.hostOnly ? cleanDomain : `.${cleanDomain}`;
    const hasExpiry = entry.expires && entry.expires !== 'Infinity';
    const expMs = hasExpiry ? Date.parse(entry.expires) : NaN;
    const session = !Number.isFinite(expMs);
    out.push({
      name: String(entry.key),
      value: String(entry.value ?? ''),
      domain,
      path: String(entry.path || '/'),
      secure: Boolean(entry.secure),
      httpOnly: Boolean(entry.httpOnly),
      session,
      ...(session ? {} : { expirationDate: Math.floor(expMs / 1000) }),
      ...(entry.sameSite ? { sameSite: String(entry.sameSite) } : {}),
    });
  }
  out.sort((a, b) =>
    `${a.domain}|${a.path}|${a.name}`.localeCompare(`${b.domain}|${b.path}|${b.name}`)
  );
  return out;
}

function cookieFingerprint(cookies) {
  return JSON.stringify(
    cookies.map(c => [c.domain, c.path, c.name, c.value, c.expirationDate ?? null])
  );
}

function extractSetCookies(headers) {
  if (!headers) return [];
  if (typeof headers.getSetCookie === 'function') {
    return headers.getSetCookie();
  }
  const single = typeof headers.get === 'function' ? headers.get('set-cookie') : null;
  return single ? [single] : [];
}

function normalizeHeaders(input) {
  const map = new Map();
  if (!input) return map;
  if (input instanceof Headers || typeof input.forEach === 'function') {
    input.forEach((value, key) => {
      map.set(String(key).toLowerCase(), [String(key), String(value)]);
    });
  } else if (Array.isArray(input)) {
    for (const [key, value] of input) {
      map.set(String(key).toLowerCase(), [String(key), String(value)]);
    }
  } else if (typeof input === 'object') {
    for (const [key, value] of Object.entries(input)) {
      if (value !== undefined && value !== null) {
        map.set(String(key).toLowerCase(), [String(key), String(value)]);
      }
    }
  }
  return map;
}

function createDefaultHttpFetch(proxyUrl) {
  const { Impit } = require('impit');
  const options = {
    browser: 'chrome',
    followRedirects: false,
    ...(proxyUrl ? { proxyUrl } : {}),
  };
  const client = new Impit(options);
  return (url, init) => client.fetch(url, init);
}

function createPoshmarkFetch({ jar, userAgent, httpFetch }) {
  const ua = String(userAgent || '').trim() || DEFAULT_USER_AGENT;

  return async function poshmarkFetch(rawUrl, init = {}) {
    let currentUrl = assertAllowedUrl(rawUrl);
    let method = String(init.method || 'GET').toUpperCase();
    let body = init.body;
    const credentials = init.credentials || 'include';
    let redirected = false;

    for (let hop = 0; hop <= MAX_REDIRECTS; hop += 1) {
      const headerMap = normalizeHeaders(init.headers);
      headerMap.delete('cookie');
      if (!headerMap.has('user-agent')) {
        headerMap.set('user-agent', ['User-Agent', ua]);
      }

      const isPoshmark = isPoshmarkCookieHost(currentUrl.hostname);
      if (isPoshmark && POSHMARK_HEADER_PATH.test(currentUrl.pathname)) {
        if (!headerMap.has('origin')) {
          headerMap.set('origin', ['Origin', 'https://poshmark.com']);
        }
        if (!headerMap.has('referer')) {
          headerMap.set('referer', ['Referer', 'https://poshmark.com/create-listing']);
        }
      }

      if (credentials !== 'omit' && isPoshmark) {
        const cookieHeader = jar.getCookieStringSync(currentUrl.href);
        if (cookieHeader) {
          headerMap.set('cookie', ['Cookie', cookieHeader]);
        }
      }

      const headersObj = {};
      for (const [, [origKey, val]] of headerMap) {
        headersObj[origKey] = val;
      }

      const requestInit = {
        method,
        headers: headersObj,
        redirect: 'manual',
        ...(body === undefined ? {} : { body }),
        ...(init.signal ? { signal: init.signal } : {}),
      };

      const response = await httpFetch(currentUrl.href, requestInit);

      if (isPoshmark) {
        for (const setCookie of extractSetCookies(response.headers)) {
          try {
            jar.setCookieSync(setCookie, currentUrl.href, { ignoreError: true });
          } catch {
            // Ignore malformed Set-Cookie header
          }
        }
      }

      const status = Number(response.status);
      const location =
        status >= 300 && status < 400 && response.headers && typeof response.headers.get === 'function'
          ? response.headers.get('location')
          : null;

      if (location && hop < MAX_REDIRECTS) {
        const nextUrl = assertAllowedUrl(new URL(location, currentUrl.href).href);
        if (status === 303 || ((status === 301 || status === 302) && method === 'POST')) {
          method = 'GET';
          body = undefined;
        }
        currentUrl = nextUrl;
        redirected = true;
        continue;
      }

      return {
        ok: status >= 200 && status < 300,
        status,
        statusText: response.statusText || '',
        url: response.url || currentUrl.href,
        redirected: Boolean(redirected || response.redirected),
        headers: response.headers,
        text: () => response.text(),
        json: () => response.json(),
        arrayBuffer: () => response.arrayBuffer(),
        blob: () => response.blob(),
        body: response.body,
      };
    }

    throw new Error('Too many redirects from marketplace.');
  };
}

function createChromeShim({ jar, initialJournals, onJournalSet, onJournalRemove }) {
  const store = new Map();
  if (initialJournals && typeof initialJournals === 'object') {
    for (const [k, v] of Object.entries(initialJournals)) {
      store.set(k, structuredClone(v));
    }
  }

  return {
    runtime: { id: 'anteroom-cloud-runner' },
    declarativeNetRequest: {
      updateSessionRules: async () => undefined,
    },
    cookies: {
      get: async ({ url, name }) => {
        if (!url || !name) return null;
        let parsed;
        try {
          parsed = new URL(url);
        } catch {
          return null;
        }
        if (!isPoshmarkCookieHost(parsed.hostname)) return null;
        const cookies = jar.getCookiesSync(parsed.href);
        const match = cookies.find(c => c.key === name);
        if (!match) return null;
        const expMs = match.expires && match.expires !== 'Infinity' ? match.expires.getTime() : NaN;
        const session = !Number.isFinite(expMs);
        return {
          name: match.key,
          value: match.value,
          domain: match.hostOnly ? match.domain : `.${match.domain}`,
          path: match.path || '/',
          secure: Boolean(match.secure),
          httpOnly: Boolean(match.httpOnly),
          session,
          ...(session ? {} : { expirationDate: Math.floor(expMs / 1000) }),
        };
      },
    },
    storage: {
      local: {
        get: async keys => {
          if (keys === null || keys === undefined) {
            return Object.fromEntries(Array.from(store.entries(), ([k, v]) => [k, structuredClone(v)]));
          }
          if (typeof keys === 'string') {
            return store.has(keys) ? { [keys]: structuredClone(store.get(keys)) } : {};
          }
          if (Array.isArray(keys)) {
            const out = {};
            for (const k of keys) {
              if (store.has(k)) out[k] = structuredClone(store.get(k));
            }
            return out;
          }
          if (typeof keys === 'object') {
            const out = {};
            for (const [k, fallback] of Object.entries(keys)) {
              out[k] = store.has(k) ? structuredClone(store.get(k)) : fallback;
            }
            return out;
          }
          return {};
        },
        set: async items => {
          if (!items || typeof items !== 'object') return;
          const durable = {};
          for (const [k, v] of Object.entries(items)) {
            if (isDurableKey(k)) {
              durable[k] = structuredClone(v);
            }
          }
          if (Object.keys(durable).length > 0 && typeof onJournalSet === 'function') {
            await onJournalSet(durable);
          }
          for (const [k, v] of Object.entries(items)) {
            store.set(k, structuredClone(v));
          }
        },
        remove: async keys => {
          const list = Array.isArray(keys) ? keys : [keys];
          const durableKeys = list.filter(k => typeof k === 'string' && isDurableKey(k));
          if (durableKeys.length > 0 && typeof onJournalRemove === 'function') {
            await onJournalRemove(durableKeys);
          }
          for (const k of list) {
            store.delete(k);
          }
        },
      },
    },
    tabs: {
      get: async () => {
        throw new Error('Tabs are unavailable in cloud marketplace mode.');
      },
      query: async () => [],
      update: async () => {
        throw new Error('Tabs are unavailable in cloud marketplace mode.');
      },
    },
    scripting: {
      executeScript: async () => [],
    },
  };
}

function defaultExtensionDir() {
  // Expected repo layout: anteroom/server/anteroom/cloud_runner/run-poshmark.cjs -> ../../../../anteroom-extension
  const candidates = [
    process.env.ANTEROOM_EXTENSION_DIR,
    path.resolve(__dirname, '../../../../anteroom-extension'),
    path.resolve(__dirname, '../../../anteroom-extension'),
    '/app/anteroom-extension',
  ].filter(Boolean);
  for (const dir of candidates) {
    if (fs.existsSync(path.join(dir, 'background', 'poshmark-native.js'))) {
      return dir;
    }
  }
  throw new Error('Could not locate anteroom-extension/background/poshmark-native.js');
}

function enforcePoshmarkConfirmation(command) {
  const type = String(command?.type || '');
  const p = command?.payload || {};
  if (type === 'marketplace.execute') {
    if (p.marketplace !== 'poshmark') {
      return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Cloud runner only supports Poshmark.' };
    }
    if ((p.action === 'list' || p.action === 'update') && p.confirm !== true) {
      return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Confirm this Poshmark action before running.' };
    }
    if (p.listing?.poshmark_specifics?.videoId) {
      return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Poshmark Cloud cannot send the selected video. Remove it or use Chrome.' };
    }
  } else if (type === 'marketplace.delete') {
    if (p.marketplace !== 'poshmark') {
      return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Cloud runner only supports Poshmark.' };
    }
    if (p.confirm !== true) {
      return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Confirm deleting this Poshmark listing.' };
    }
  } else if (type === 'marketplace.promote') {
    if (p.marketplace !== 'poshmark') {
      return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Cloud runner only supports Poshmark.' };
    }
    if (p.confirm !== true) {
      return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Confirm this Poshmark share or offer.' };
    }
    if (p.action === 'follow') {
      const cfg = p.follow_config || {};
      if (Number(cfg.new_active_sellers_cap || 0) > 1000 || Number(cfg.unfollow_cap || 0) > 1000) {
        return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Follow and unfollow caps cannot exceed 1,000 per day.' };
      }
    }
  } else if (
    [
      'marketplace.sales',
      'marketplace.sales_history',
      'marketplace.import.page',
      'marketplace.import.listing',
      'marketplace.promotion_check',
      'marketplace.like_events',
      'marketplace.active_offers',
    ].includes(type)
  ) {
    if (p.marketplace !== 'poshmark') {
      return { ok: false, safe_to_retry: true, error_code: 'rejected', error: 'Cloud runner only supports Poshmark.' };
    }
  }
  return null;
}

async function executePoshmarkOperation({
  command,
  cookies = [],
  userAgent = DEFAULT_USER_AGENT,
  proxyUrl = null,
  photos = [],
  journals = {},
  extensionDir = null,
  httpFetch = null,
  onJournalSet = null,
  onJournalRemove = null,
  onContextReady = null,
}) {
  const refusal = enforcePoshmarkConfirmation(command);
  if (refusal) {
    return { result: refusal, cookies_changed: false, cookies };
  }

  const jar = buildCookieJar(cookies);
  const initialSnapshot = cookieFingerprint(exportCookies(jar));

  const transport = httpFetch || createDefaultHttpFetch(proxyUrl);
  const fetchFn = createPoshmarkFetch({ jar, userAgent, httpFetch: transport });
  const chrome = createChromeShim({
    jar,
    initialJournals: journals,
    onJournalSet,
    onJournalRemove,
  });

  const stderrConsole = {
    log: (...args) => process.stderr.write(`[cloud-poshmark] ${args.join(' ')}\n`),
    info: (...args) => process.stderr.write(`[cloud-poshmark] ${args.join(' ')}\n`),
    warn: (...args) => process.stderr.write(`[cloud-poshmark] WARN ${args.join(' ')}\n`),
    error: (...args) => process.stderr.write(`[cloud-poshmark] ERROR ${args.join(' ')}\n`),
  };

  const sandbox = {
    console: stderrConsole,
    chrome,
    fetch: fetchFn,
    Headers,
    Request,
    Response,
    FormData,
    Blob,
    File,
    URL,
    URLSearchParams,
    AbortController,
    AbortSignal,
    TextEncoder,
    TextDecoder,
    Uint8Array,
    ArrayBuffer,
    atob,
    btoa,
    crypto: globalThis.crypto || crypto.webcrypto,
    structuredClone,
    setTimeout,
    clearTimeout,
    // Ensure marketplace-network's ensureMarketplaceRequestContext is a clean no-op
    // because createPoshmarkFetch already applies Origin/Referer/User-Agent.
    ensureMarketplaceRequestContext: async () => undefined,
    STUDIO_URL: 'https://invalid.local',
    send: () => undefined,
  };
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;

  const context = vm.createContext(sandbox);
  const baseDir = path.join(extensionDir || defaultExtensionDir(), 'background');
  for (const scriptName of EXTENSION_SCRIPTS) {
    const fullPath = path.join(baseDir, scriptName);
    const code = fs.readFileSync(fullPath, 'utf8');
    vm.runInContext(code, context, { filename: scriptName });
  }

  if (typeof onContextReady === 'function') {
    onContextReady(context);
  }

  const cmdType = String(command?.type || '');
  let result;
  if (cmdType === 'marketplace.sales') {
    result = await context.readMarketplaceSales('poshmark', command.payload?.listing_ids || []);
  } else if (cmdType === 'marketplace.sessions') {
    const summary = await context.readExtensionSession('poshmark');
    result = { ok: true, sessions: { poshmark: summary } };
  } else if (cmdType === 'marketplace.verify_session') {
    const summary = await context.readExtensionSession('poshmark');
    if (summary.state !== 'signed_in') {
      result = { ok: true, session: summary };
    } else {
      try {
        await context.poshmarkCsrf(Date.now() + 15000);
        result = { ok: true, session: summary };
      } catch (err) {
        if (err?.code === 'signed_out') {
          result = { ok: true, session: { ...summary, state: 'expired', expiring: false } };
        } else {
          result = { ok: false, error_code: err?.code || 'network', error: String(err?.message || err) };
        }
      }
    }
  } else if (cmdType === 'marketplace.form') {
    const form = await context.readPoshmarkEditorForm(command.payload?.listing || {}, Date.now() + 25000);
    result = { ok: true, form };
  } else {
    result = await context.runCloudMarketplaceCommand(command, Array.isArray(photos) ? photos : []);
  }

  const exported = exportCookies(jar);
  const cookiesChanged = cookieFingerprint(exported) !== initialSnapshot;
  return { result, cookies_changed: cookiesChanged, cookies: exported };
}

async function runCli() {
  const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  const pendingAcks = new Map();
  let seq = 0;
  let activeContext = null;
  let started = false;

  const writeLine = (obj, done) => {
    process.stdout.write(`${JSON.stringify(obj)}\n`, done);
  };

  const requestJournalSync = payload =>
    new Promise((resolve, reject) => {
      seq += 1;
      const mySeq = seq;
      pendingAcks.set(mySeq, { resolve, reject });
      writeLine({ ...payload, seq: mySeq });
    });

  rl.on('line', line => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg;
    try {
      msg = JSON.parse(trimmed);
    } catch {
      return;
    }

    if (msg.type === 'journal_ack') {
      const waiter = pendingAcks.get(msg.seq);
      if (waiter) {
        pendingAcks.delete(msg.seq);
        if (msg.ok) waiter.resolve();
        else waiter.reject(new Error(msg.error || 'Failed to persist operation journal.'));
      }
      return;
    }

    if (msg.type === 'cancel') {
      if (activeContext && typeof activeContext.cancelNativeMarketplaceOperation === 'function') {
        void activeContext.cancelNativeMarketplaceOperation(String(msg.job_id || ''));
      }
      return;
    }

    if (msg.type === 'run' && !started) {
      started = true;
      void (async () => {
        try {
          const outcome = await executePoshmarkOperation({
            command: msg.command,
            cookies: msg.cookies || [],
            userAgent: msg.user_agent || DEFAULT_USER_AGENT,
            proxyUrl: msg.proxy_url || null,
            photos: msg.photos || [],
            journals: msg.journals || {},
            extensionDir: msg.extension_dir || null,
            onJournalSet: entries => requestJournalSync({ type: 'journal_set', entries }),
            onJournalRemove: keys => requestJournalSync({ type: 'journal_remove', keys }),
            onContextReady: ctx => {
              activeContext = ctx;
            },
          });
          writeLine({ type: 'result', ...outcome }, () => process.exit(0));
        } catch (err) {
          writeLine({
            type: 'result',
            result: {
              ok: false,
              safe_to_retry: false,
              error_code: err?.code || 'network',
              error: String(err?.message || err),
            },
            cookies_changed: false,
            cookies: [],
          }, () => process.exit(1));
        }
      })();
    }
  });

  rl.on('close', () => {
    for (const waiter of pendingAcks.values()) {
      waiter.reject(new Error('Parent closed journal channel.'));
    }
    pendingAcks.clear();
  });
}

if (require.main === module) {
  void runCli();
}

module.exports = {
  executePoshmarkOperation,
  isAllowedHost,
  isPoshmarkCookieHost,
  assertAllowedUrl,
  buildCookieJar,
  exportCookies,
  createPoshmarkFetch,
  DEFAULT_USER_AGENT,
};
