// Etsy calls that need the shared secret, which never leaves the relay:
// sign-in code exchange, refresh, shop lookup, and forwarding Anteroom's
// API calls with the app's x-api-key attached.

const ETSY_API = "https://api.etsy.com";

// Listings (drafts, publish, delist), sales, and shop details. No shipping
// addresses or email.
export const ETSY_SCOPES = ["listings_r", "listings_w", "listings_d", "transactions_r", "shops_r"];

export function etsyConfigured(env) {
  return Boolean(env.ETSY_KEYSTRING && env.ETSY_SHARED_SECRET);
}

function apiKey(env) {
  return `${env.ETSY_KEYSTRING}:${env.ETSY_SHARED_SECRET}`;
}

export function etsyCallbackUrl(origin) {
  return `${origin}/oauth/etsy/callback`;
}

export function etsyAuthorizeUrl(env, { state, challenge, redirectUri }) {
  const url = new URL("https://www.etsy.com/oauth/connect");
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", env.ETSY_KEYSTRING);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("scope", ETSY_SCOPES.join(" "));
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  return url.toString();
}

async function tokenRequest(env, params) {
  const response = await fetch(`${ETSY_API}/v3/public/oauth/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "x-api-key": apiKey(env) },
    body: new URLSearchParams({ client_id: env.ETSY_KEYSTRING, ...params }).toString(),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(`Etsy token request failed: ${response.status} ${body.error ?? ""}`.trim());
    error.status = response.status;
    throw error;
  }
  return body;
}

export function etsyExchangeCode(env, { code, verifier, redirectUri }) {
  return tokenRequest(env, {
    grant_type: "authorization_code",
    code,
    code_verifier: verifier,
    redirect_uri: redirectUri,
  });
}

export function etsyRefresh(env, refreshToken) {
  return tokenRequest(env, { grant_type: "refresh_token", refresh_token: refreshToken });
}

// Etsy access tokens start with the numeric user ID: "12345678.abc...".
export function etsyUserIdFromToken(accessToken) {
  const match = /^(\d+)\./.exec(accessToken ?? "");
  return match ? match[1] : null;
}

export async function etsyShop(env, accessToken) {
  const userId = etsyUserIdFromToken(accessToken);
  if (!userId) throw new Error("Etsy token has no user ID");
  const response = await fetch(`${ETSY_API}/v3/application/users/${userId}/shops`, {
    headers: { authorization: `Bearer ${accessToken}`, "x-api-key": apiKey(env) },
  });
  if (!response.ok) throw new Error(`Etsy shop lookup failed: ${response.status}`);
  const body = await response.json();
  const shop = Array.isArray(body.results) ? body.results[0] : body;
  if (!shop?.shop_id) throw new Error("This Etsy account has no shop");
  return { userId, shopId: String(shop.shop_id), shopName: typeof shop.shop_name === "string" ? shop.shop_name : null };
}

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "if-none-match"];
const FORWARDED_RESPONSE_HEADERS = ["content-type", "etag", "x-remaining-this-second", "x-remaining-today", "retry-after"];

// Forwards one call to api.etsy.com/v3/application/... with the app key.
export async function etsyForward(env, request, path, etsyToken) {
  const source = new URL(request.url);
  const target = new URL(`${ETSY_API}/v3/application/${path}`);
  target.search = source.search;
  const headers = new Headers({ authorization: `Bearer ${etsyToken}`, "x-api-key": apiKey(env) });
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = !["GET", "HEAD"].includes(request.method);
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
  });
  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}
