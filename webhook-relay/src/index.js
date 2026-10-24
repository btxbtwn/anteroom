import { extractEbayEvent, extractEtsyEvent } from "./extract.js";
import { Inbox } from "./inbox.js";
import {
  ebayChallengeResponse,
  parseEbaySignatureHeader,
  timingSafeEqual,
  verifyEbaySignature,
  verifyEtsySignature,
} from "./verify.js";

export { Inbox };

const MAX_BODY_BYTES = 64 * 1024;
const KEY_TTL_MS = 60 * 60 * 1000;

const EBAY_HOSTS = {
  production: "https://api.ebay.com",
  sandbox: "https://api.sandbox.ebay.com",
};

// Per-isolate caches. eBay asks for public keys to be cached about an hour.
const publicKeys = new Map();
let appToken = null;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function inbox(env) {
  return env.INBOX.get(env.INBOX.idFromName("studio"));
}

async function readBody(request) {
  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > MAX_BODY_BYTES) return null;
  const text = await request.text();
  return new TextEncoder().encode(text).length > MAX_BODY_BYTES ? null : text;
}

function ebayHost(env) {
  return EBAY_HOSTS[env.EBAY_ENV] ?? EBAY_HOSTS.production;
}

async function ebayAppToken(env) {
  if (appToken && appToken.expiresAt > Date.now() + 60_000) return appToken.value;
  const response = await fetch(`${ebayHost(env)}/identity/v1/oauth2/token`, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      authorization: `Basic ${btoa(`${env.EBAY_CLIENT_ID}:${env.EBAY_CLIENT_SECRET}`)}`,
    },
    body: "grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope",
  });
  if (!response.ok) throw new Error(`eBay token request failed: ${response.status}`);
  const body = await response.json();
  appToken = { value: body.access_token, expiresAt: Date.now() + body.expires_in * 1000 };
  return appToken.value;
}

async function ebayPublicKey(env, kid) {
  const cached = publicKeys.get(kid);
  if (cached && cached.expiresAt > Date.now()) return cached.pem;
  const token = await ebayAppToken(env);
  const response = await fetch(
    `${ebayHost(env)}/commerce/notification/v1/public_key/${encodeURIComponent(kid)}`,
    { headers: { authorization: `Bearer ${token}` } },
  );
  if (!response.ok) throw new Error(`eBay public key request failed: ${response.status}`);
  const body = await response.json();
  publicKeys.set(kid, { pem: body.key, expiresAt: Date.now() + KEY_TTL_MS });
  return body.key;
}

async function handleEbayChallenge(request, env, url) {
  const challengeCode = url.searchParams.get("challenge_code");
  if (!challengeCode) return json({ error: "missing challenge_code" }, 400);
  if (!env.EBAY_VERIFICATION_TOKEN) return json({ error: "eBay is not configured" }, 503);
  const endpoint = env.EBAY_ENDPOINT_URL || `${url.origin}${url.pathname}`;
  const challengeResponse = await ebayChallengeResponse(challengeCode, env.EBAY_VERIFICATION_TOKEN, endpoint);
  return json({ challengeResponse });
}

async function handleEbayNotification(request, env) {
  if (!env.EBAY_CLIENT_ID || !env.EBAY_CLIENT_SECRET) return json({ error: "eBay is not configured" }, 503);
  const header = parseEbaySignatureHeader(request.headers.get("x-ebay-signature"));
  if (!header) return json({ error: "bad signature header" }, 412);
  const rawBody = await readBody(request);
  if (rawBody === null) return json({ error: "body too large" }, 413);
  let publicKeyPem;
  try {
    publicKeyPem = await ebayPublicKey(env, header.kid);
  } catch (error) {
    console.error("[EBAY]", error.message);
    return json({ error: "could not fetch eBay public key" }, 500);
  }
  if (!(await verifyEbaySignature({ rawBody, signature: header.signature, publicKeyPem }))) {
    return json({ error: "signature mismatch" }, 412);
  }
  const event = extractEbayEvent(JSON.parse(rawBody));
  if (!event) return json({ error: "unrecognised notification" }, 400);
  await inbox(env).add(event);
  return new Response(null, { status: 204 });
}

async function handleEtsy(request, env) {
  if (!env.ETSY_WEBHOOK_SECRET) return json({ error: "Etsy is not configured" }, 503);
  const rawBody = await readBody(request);
  if (rawBody === null) return json({ error: "body too large" }, 413);
  const id = request.headers.get("webhook-id");
  const ok = await verifyEtsySignature({
    id,
    timestamp: request.headers.get("webhook-timestamp"),
    signatureHeader: request.headers.get("webhook-signature"),
    rawBody,
    secret: env.ETSY_WEBHOOK_SECRET,
  });
  if (!ok) return json({ error: "signature mismatch" }, 401);
  let payload;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return json({ error: "bad json" }, 400);
  }
  const event = extractEtsyEvent(payload, id);
  if (!event) return json({ error: "unrecognised event" }, 400);
  await inbox(env).add(event);
  return json({ ok: true });
}

function studioAuthorized(request, env) {
  const header = request.headers.get("authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  return Boolean(env.STUDIO_TOKEN) && token.length > 0 && timingSafeEqual(token, env.STUDIO_TOKEN);
}

async function handleStudio(request, env, url) {
  if (!studioAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
  const box = inbox(env);
  if (url.pathname === "/studio/stream") {
    if (request.headers.get("upgrade") !== "websocket") return json({ error: "expected websocket" }, 426);
    return box.fetch(request);
  }
  if (url.pathname === "/studio/events" && request.method === "GET") {
    const after = Number(url.searchParams.get("after") ?? 0) || 0;
    const limit = Number(url.searchParams.get("limit") ?? 200) || 200;
    return json({ events: await box.list(after, limit) });
  }
  if (url.pathname === "/studio/ack" && request.method === "POST") {
    const body = await request.json().catch(() => null);
    if (!Number.isInteger(body?.through)) return json({ error: "through must be an integer" }, 400);
    return json({ pending: await box.ack(body.through) });
  }
  return json({ error: "not found" }, 404);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/" || url.pathname === "/health") return json({ ok: true });
    if (url.pathname === "/ebay") {
      if (request.method === "GET") return handleEbayChallenge(request, env, url);
      if (request.method === "POST") return handleEbayNotification(request, env);
    }
    if (url.pathname === "/etsy" && request.method === "POST") return handleEtsy(request, env);
    if (url.pathname.startsWith("/studio/")) return handleStudio(request, env, url);
    return json({ error: "not found" }, 404);
  },
};
