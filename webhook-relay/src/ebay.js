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

// What a seller grants Anteroom: see sales, know who they are, and let the
// relay subscribe them to sale notifications.
export const EBAY_USER_SCOPES = [
  APP_SCOPE,
  "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
  "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
  "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription",
];

export const EBAY_SALE_TOPICS = ["ORDER_CONFIRMATION"];

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

export function ebayAuthorizeUrl(env, state) {
  const url = new URL(`${hosts(env).auth}/oauth2/authorize`);
  url.searchParams.set("client_id", env.EBAY_CLIENT_ID);
  url.searchParams.set("redirect_uri", env.EBAY_RUNAME);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", EBAY_USER_SCOPES.join(" "));
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
    scope: EBAY_USER_SCOPES.join(" "),
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
