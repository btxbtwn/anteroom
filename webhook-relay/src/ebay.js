// eBay calls that need the app's Cert ID, which never leaves the relay:
// app tokens, signing keys, sign-in code exchange, refresh, and the
// per-seller ORDER_CONFIRMATION subscription.

const HOSTS = {
  production: { api: "https://api.ebay.com", apiz: "https://apiz.ebay.com", auth: "https://auth.ebay.com" },
  sandbox: {
    api: "https://api.sandbox.ebay.com",
    apiz: "https://apiz.sandbox.ebay.com",
    auth: "https://auth.sandbox.ebay.com",
  },
};

const APP_SCOPE = "https://api.ebay.com/oauth/api_scope";

// Sales, identity, notifications, and seller-approved offers. Offer calls run
// on the seller's Mac; the relay never sends offers or stores listing data.
export const EBAY_USER_SCOPES = [
  APP_SCOPE,
  "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
  "https://api.ebay.com/oauth/api_scope/sell.inventory",
  "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
  "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription",
];

export const EBAY_SALE_TOPICS = ["ORDER_CONFIRMATION"];

// Feature grants are opt-in so a restricted feature cannot block ordinary
// sign-in or sale alerts. A seller can grant each group separately.
export const EBAY_FEATURE_SCOPES = {
  analytics: ["sell.analytics.readonly"],
  // The production keyset has sell.finances, but not the separate detailed
  // earnings scope. Requesting that scope rejects the entire consent flow.
  finances: ["sell.finances"],
  orders: ["sell.fulfillment"],
  disputes: ["sell.payment.dispute"],
  messages: ["commerce.message"],
  feedback: ["commerce.feedback"],
  marketing: ["sell.marketing"],
  listings: ["sell.account.readonly"],
};

const KEY_TTL_MS = 60 * 60 * 1000;
const publicKeys = new Map();
let appToken = null;

function hosts(env) {
  return HOSTS[env.EBAY_ENV] ?? HOSTS.production;
}

export function ebayConfigured(env) {
  return Boolean(env.EBAY_CLIENT_ID && env.EBAY_CLIENT_SECRET);
}

function basicAuth(env) {
  return `Basic ${btoa(`${env.EBAY_CLIENT_ID}:${env.EBAY_CLIENT_SECRET}`)}`;
}

async function tokenRequest(env, params) {
  const response = await fetch(`${hosts(env).api}/identity/v1/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", authorization: basicAuth(env) },
    body: new URLSearchParams(params).toString(),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(`eBay token request failed: ${response.status} ${body.error ?? ""}`.trim());
    error.status = response.status;
    error.ebay = body.error;
    throw error;
  }
  return body;
}

export async function ebayAppToken(env) {
  if (appToken && appToken.expiresAt > Date.now() + 60_000) return appToken.value;
  const body = await tokenRequest(env, { grant_type: "client_credentials", scope: APP_SCOPE });
  appToken = { value: body.access_token, expiresAt: Date.now() + body.expires_in * 1000 };
  return appToken.value;
}

// The shared token can administer app-wide notifications. Never return it to a
// device; forward only these public research operations without storing data.
export async function ebayResearch(env, input) {
  const params = input?.params ?? {};
  if (!params || typeof params !== "object" || Array.isArray(params)) return new Response(null, { status: 400 });
  const routes = {
    categories: ["GET", "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions", ["q"]],
    aspects: ["GET", "/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category", ["category_id"]],
    search: ["GET", "/buy/browse/v1/item_summary/search", ["q", "limit", "offset", "filter"]],
    image: ["POST", "/buy/browse/v1/item_summary/search_by_image", ["limit", "offset", "filter"]],
    ad_metadata: ["GET", "/sell/marketing/v1/ad_report_metadata/CAMPAIGN_PERFORMANCE_SUMMARY_REPORT", []],
  };
  const selected = Object.hasOwn(routes, input?.operation) ? routes[input.operation] : null;
  if (!selected || Object.keys(params).some((key) => !selected[2].includes(key))) return new Response(null, { status: 400 });
  const [method, path] = selected;
  const url = new URL(hosts(env).api + path);
  for (const [key, value] of Object.entries(params)) {
    if (!["string", "number"].includes(typeof value) || String(value).length > 500) return new Response(null, { status: 400 });
    url.searchParams.set(key, String(value));
  }
  if (input.operation === "image" && (typeof input.image !== "string" || input.image.length > 11000000 || !/^[A-Za-z0-9+/]+={0,2}$/.test(input.image))) return new Response(null, { status: 400 });
  const response = await fetch(url, {
    method, redirect: "manual",
    headers: { authorization: `Bearer ${await ebayAppToken(env)}`, "content-type": "application/json", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US" },
    ...(method === "POST" ? { body: JSON.stringify({ image: input.image }) } : {}),
  });
  // Do not forward provider headers, credentials, or private error echoes.
  const body = response.ok ? await response.text() : JSON.stringify({ error: "eBay research unavailable" });
  return new Response(body, { status: response.status >= 300 && response.status < 400 ? 502 : response.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}

export async function ebayPublicKey(env, kid) {
  const cached = publicKeys.get(kid);
  if (cached && cached.expiresAt > Date.now()) return cached.pem;
  const token = await ebayAppToken(env);
  const response = await fetch(`${hosts(env).api}/commerce/notification/v1/public_key/${encodeURIComponent(kid)}`, {
    headers: { authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`eBay public key request failed: ${response.status}`);
  const body = await response.json();
  publicKeys.set(kid, { pem: body.key, expiresAt: Date.now() + KEY_TTL_MS });
  return body.key;
}

export function ebayAuthorizeUrl(env, state, features = []) {
  if (features.some((name) => !Object.hasOwn(EBAY_FEATURE_SCOPES, name))) throw new Error("Unknown eBay permission group");
  const url = new URL(`${hosts(env).auth}/oauth2/authorize`);
  url.searchParams.set("client_id", env.EBAY_CLIENT_ID);
  url.searchParams.set("redirect_uri", env.EBAY_RUNAME);
  url.searchParams.set("response_type", "code");
  const extra = features.flatMap((name) => EBAY_FEATURE_SCOPES[name].map((scope) => `${APP_SCOPE}/${scope}`));
  url.searchParams.set("scope", [...new Set([...EBAY_USER_SCOPES, ...extra])].join(" "));
  url.searchParams.set("state", state);
  return url.toString();
}

export function ebayExchangeCode(env, code) {
  return tokenRequest(env, { grant_type: "authorization_code", code, redirect_uri: env.EBAY_RUNAME });
}

export function ebayRefresh(env, refreshToken) {
  return tokenRequest(env, {
    grant_type: "refresh_token",
    refresh_token: refreshToken,
    // Refresh the scopes originally granted. New permissions require sign-in;
    // adding them here would also break sales for existing connections.
  });
}

export async function ebayUser(env, accessToken) {
  const response = await fetch(`${hosts(env).apiz}/commerce/identity/v1/user/`, {
    headers: { authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`eBay user lookup failed: ${response.status}`);
  const body = await response.json();
  if (typeof body.userId !== "string") throw new Error("eBay user lookup returned no userId");
  return { userId: body.userId, username: typeof body.username === "string" ? body.username : null };
}

async function notificationApi(env, token, path, init = {}) {
  const response = await fetch(`${hosts(env).api}/commerce/notification/v1${path}`, {
    ...init,
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json", ...(init.headers ?? {}) },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(`eBay ${init.method ?? "GET"} ${path} failed: ${response.status}`);
  return { body, location: response.headers.get("location") };
}

// One destination per app: this relay's /ebay endpoint.
export async function ensureEbayDestination(env, endpoint) {
  const token = await ebayAppToken(env);
  const { body } = await notificationApi(env, token, "/destination?limit=100");
  const existing = (body.destinations ?? []).find((d) => d.deliveryConfig?.endpoint === endpoint);
  if (existing) return existing.destinationId;
  const created = await notificationApi(env, token, "/destination", {
    method: "POST",
    body: JSON.stringify({
      name: "Anteroom relay",
      status: "ENABLED",
      deliveryConfig: { endpoint, verificationToken: env.EBAY_VERIFICATION_TOKEN },
    }),
  });
  return created.location?.split("/").pop() ?? null;
}

// Sale topics are per seller, so each subscription is made with that
// seller's own token. Existing ones are left as they are.
export async function ensureEbaySubscriptions(env, userToken, destinationId) {
  const { body } = await notificationApi(env, userToken, "/subscription?limit=100");
  const have = new Set((body.subscriptions ?? []).map((s) => s.topicId));
  const created = [];
  for (const topicId of EBAY_SALE_TOPICS) {
    if (have.has(topicId)) continue;
    await notificationApi(env, userToken, "/subscription", {
      method: "POST",
      body: JSON.stringify({
        topicId,
        status: "ENABLED",
        destinationId,
        payload: { format: "JSON", schemaVersion: "1.0", deliveryProtocol: "HTTPS" },
      }),
    });
    created.push(topicId);
  }
  return created;
}
