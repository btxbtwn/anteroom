import { exports } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { bytesToBase64, pkceChallenge } from "../../src/verify.js";

const ORIGIN = "https://relay.test";
const ETSY_SECRET = `whsec_${btoa("0123456789abcdef0123456789abcdef")}`;

const relay = (path, init) => exports.default.fetch(new Request(`${ORIGIN}${path}`, init));
const authed = (token, init = {}) => ({ ...init, headers: { authorization: `Bearer ${token}`, ...(init.headers ?? {}) } });

// --- Outbound calls to eBay and Etsy ---------------------------------------

let routes;
let calls;

function route(method, pattern, respond) {
  routes.push({ method, pattern, respond });
}

beforeEach(() => {
  routes = [];
  calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init = {}) => {
    const request = new Request(input, init);
    const body = ["GET", "HEAD"].includes(request.method) ? "" : await request.clone().text();
    calls.push({ method: request.method, url: request.url, headers: request.headers, body });
    const match = routes.findLast((r) => r.method === request.method && r.pattern.test(request.url));
    if (!match) return new Response(`unmocked ${request.method} ${request.url}`, { status: 599 });
    const out = await match.respond({ request, body, url: new URL(request.url) });
    return out instanceof Response ? out : Response.json(out);
  });
});

afterEach(() => vi.restoreAllMocks());

// --- Helpers ----------------------------------------------------------------

let nextIp = 0;
async function register() {
  nextIp += 1;
  const response = await relay("/devices", { method: "POST", headers: { "cf-connecting-ip": `10.0.0.${nextIp}` } });
  expect(response.status).toBe(201);
  return response.json();
}

async function events(token) {
  const response = await relay("/studio/events", authed(token));
  return (await response.json()).events;
}

async function etsySignature(id, timestamp, body) {
  const key = await crypto.subtle.importKey(
    "raw",
    Uint8Array.from(atob(ETSY_SECRET.slice(6)), (c) => c.charCodeAt(0)),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${id}.${timestamp}.${body}`));
  return `v1,${bytesToBase64(mac)}`;
}

async function postEtsyWebhook(id, payload) {
  const body = JSON.stringify(payload);
  const timestamp = String(Math.floor(Date.now() / 1000));
  return relay("/etsy", {
    method: "POST",
    body,
    headers: {
      "webhook-id": id,
      "webhook-timestamp": timestamp,
      "webhook-signature": await etsySignature(id, timestamp, body),
    },
  });
}

function rawToDer(raw) {
  const half = raw.length / 2;
  const integer = (bytes) => {
    let i = 0;
    while (i < bytes.length - 1 && bytes[i] === 0) i += 1;
    let value = bytes.slice(i);
    if (value[0] & 0x80) value = Uint8Array.of(0, ...value);
    return [0x02, value.length, ...value];
  };
  const body = [...integer(raw.slice(0, half)), ...integer(raw.slice(half))];
  return Uint8Array.of(0x30, body.length, ...body);
}

let ebayKey;
async function ebaySigner() {
  if (!ebayKey) {
    const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
    const spki = new Uint8Array(await crypto.subtle.exportKey("spki", pair.publicKey));
    ebayKey = { pair, pem: `-----BEGIN PUBLIC KEY-----${bytesToBase64(spki)}-----END PUBLIC KEY-----` };
  }
  return ebayKey;
}

async function postEbayNotification(payload) {
  const { pair } = await ebaySigner();
  const body = JSON.stringify(payload);
  const raw = new Uint8Array(
    await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-1" }, pair.privateKey, new TextEncoder().encode(body)),
  );
  const header = btoa(JSON.stringify({ alg: "ecdsa", kid: "kid-1", signature: bytesToBase64(rawToDer(raw)), digest: "SHA1" }));
  return relay("/ebay", { method: "POST", body, headers: { "x-ebay-signature": header } });
}

async function mockEbayBasics() {
  const { pem } = await ebaySigner();
  route("POST", /api\.ebay\.com\/identity\/v1\/oauth2\/token/, ({ body }) => {
    const params = new URLSearchParams(body);
    if (params.get("grant_type") === "client_credentials") return { access_token: "app-token", expires_in: 7200 };
    if (params.get("grant_type") === "authorization_code") {
      return { access_token: "ebay-user-token", refresh_token: "ebay-refresh", expires_in: 7200, refresh_token_expires_in: 47304000 };
    }
    if (params.get("refresh_token") === "dead") return new Response('{"error":"invalid_grant"}', { status: 400 });
    return { access_token: "ebay-user-token-2", expires_in: 7200 };
  });
  route("GET", /notification\/v1\/public_key\/kid-1/, () => ({ algorithm: "ECDSA", digest: "SHA1", key: pem }));
}

async function connectEbay(token, userId = "seller-1") {
  await mockEbayBasics();
  route("GET", /apiz\.ebay\.com\/commerce\/identity\/v1\/user/, () => ({ userId, username: `name-${userId}` }));
  route("GET", /notification\/v1\/destination/, () => ({ destinations: [] }));
  route("POST", /notification\/v1\/destination$/, () =>
    new Response(null, { status: 201, headers: { location: "https://api.ebay.com/commerce/notification/v1/destination/dest-1" } }),
  );
  route("GET", /notification\/v1\/subscription/, () => ({ subscriptions: [] }));
  route("POST", /notification\/v1\/subscription$/, () => new Response(null, { status: 201 }));
  const start = await (await relay("/studio/oauth/ebay/start", authed(token, { method: "POST" }))).json();
  const callback = await relay(`/oauth/ebay/callback?code=abc&state=${start.state}`);
  expect(callback.status).toBe(200);
  return start;
}

async function connectEtsy(token, { userId = "777", shopId = "42" } = {}) {
  route("POST", /api\.etsy\.com\/v3\/public\/oauth\/token/, () => ({
    access_token: `${userId}.access`,
    refresh_token: `${userId}.refresh`,
    expires_in: 3600,
  }));
  route("GET", new RegExp(`api\\.etsy\\.com/v3/application/users/${userId}/shops`), () => ({ shop_id: Number(shopId), shop_name: `Shop${shopId}` }));
  const start = await (await relay("/studio/oauth/etsy/start", authed(token, { method: "POST" }))).json();
  const callback = await relay(`/oauth/etsy/callback?code=etsy-code&state=${start.state}`);
  expect(callback.status).toBe(200);
  return start;
}

// --- Tests -------------------------------------------------------------------

describe("devices", () => {
  it("limits registrations per address", async () => {
    const attempt = () => relay("/devices", { method: "POST", headers: { "cf-connecting-ip": "10.9.9.9" } });
    const statuses = [];
    for (let i = 0; i < 6; i += 1) statuses.push((await attempt()).status);
    expect(statuses.slice(0, 5)).toEqual([201, 201, 201, 201, 201]);
    expect(statuses[5]).toBe(429);
  });

  it("registers devices with separate tokens and refuses unknown ones", async () => {
    const a = await register();
    const b = await register();
    expect(a.token).not.toBe(b.token);
    expect((await relay("/studio/events")).status).toBe(401);
    expect((await relay("/studio/events", authed("nope"))).status).toBe(401);
    expect(await events(a.token)).toEqual([]);
  });

  it("removing a device revokes its token", async () => {
    const a = await register();
    expect((await relay("/studio/device", authed(a.token, { method: "DELETE" }))).status).toBe(200);
    expect((await relay("/studio/events", authed(a.token))).status).toBe(401);
  });
});

describe("Etsy", () => {
  it("signs in with PKCE, hands the tokens over once, and links the shop", async () => {
    const a = await register();
    const start = await connectEtsy(a.token);
    const authorize = new URL(start.authorizeUrl);
    expect(authorize.origin).toBe("https://www.etsy.com");
    expect(authorize.searchParams.get("client_id")).toBe("test-keystring");
    expect(authorize.searchParams.get("redirect_uri")).toBe(`${ORIGIN}/oauth/etsy/callback`);
    expect(authorize.searchParams.get("scope")).toBe("listings_r listings_w listings_d transactions_r shops_r");

    const exchange = calls.find((c) => c.url.includes("/v3/public/oauth/token"));
    const params = new URLSearchParams(exchange.body);
    expect(await pkceChallenge(params.get("code_verifier"))).toBe(authorize.searchParams.get("code_challenge"));
    expect(exchange.headers.get("x-api-key")).toBe("test-keystring:test-shared-secret");

    const result = await relay(`/studio/oauth/result?state=${start.state}`, authed(a.token));
    expect(result.status).toBe(200);
    const body = await result.json();
    expect(body).toMatchObject({ status: "done", marketplace: "etsy", accountId: "42", shopName: "Shop42" });
    expect(body.tokens).toMatchObject({ accessToken: "777.access", refreshToken: "777.refresh" });
    expect((await relay(`/studio/oauth/result?state=${start.state}`, authed(a.token))).status).toBe(404);

    const accounts = await (await relay("/studio/accounts", authed(a.token))).json();
    expect(accounts.accounts).toEqual([expect.objectContaining({ marketplace: "etsy", accountId: "42", label: "Shop42" })]);
  });

  it("does not hand one device's sign-in to another", async () => {
    const a = await register();
    const b = await register();
    const start = await connectEtsy(a.token, { userId: "778", shopId: "43" });
    expect((await relay(`/studio/oauth/result?state=${start.state}`, authed(b.token))).status).toBe(404);
  });

  it("refuses a reused or unknown sign-in link", async () => {
    const a = await register();
    const start = await connectEtsy(a.token, { userId: "779", shopId: "44" });
    expect((await relay(`/oauth/etsy/callback?code=again&state=${start.state}`)).status).toBe(400);
    expect((await relay("/oauth/etsy/callback?code=x&state=made-up")).status).toBe(400);
  });

  it("routes each shop's webhooks to its own device", async () => {
    const a = await register();
    const b = await register();
    await connectEtsy(a.token, { userId: "780", shopId: "50" });
    await connectEtsy(b.token, { userId: "781", shopId: "51" });
    const paid = (shop) => ({ event_type: "order.paid", shop_id: shop, resource_url: `https://api.etsy.com/v3/application/shops/${shop}/receipts/9${shop}` });
    expect((await postEtsyWebhook("msg_a", paid(50))).status).toBe(200);
    expect((await postEtsyWebhook("msg_b", paid(51))).status).toBe(200);
    expect((await postEtsyWebhook("msg_x", paid(99))).status).toBe(200);
    expect((await events(a.token)).map((e) => e.ids.receipt_id[0])).toEqual(["950"]);
    expect((await events(b.token)).map((e) => e.ids.receipt_id[0])).toEqual(["951"]);
  });

  it("forwards API calls with the app key, only for the device's own Etsy account", async () => {
    const a = await register();
    const b = await register();
    await connectEtsy(a.token, { userId: "782", shopId: "52" });
    route("GET", /api\.etsy\.com\/v3\/application\/shops\/52\/receipts/, ({ request }) =>
      Response.json({ count: 0, results: [], sawAuth: request.headers.get("authorization") }),
    );
    const call = (token) =>
      relay("/studio/etsy/v3/application/shops/52/receipts?limit=5", authed(token, { headers: { "x-etsy-authorization": "Bearer 782.access" } }));
    const ok = await call(a.token);
    expect(ok.status).toBe(200);
    expect((await ok.json()).sawAuth).toBe("Bearer 782.access");
    const upstream = calls.find((c) => c.url.includes("/shops/52/receipts"));
    expect(upstream.url).toBe("https://api.etsy.com/v3/application/shops/52/receipts?limit=5");
    expect(upstream.headers.get("x-api-key")).toBe("test-keystring:test-shared-secret");
    expect((await call(b.token)).status).toBe(403);
  });

  it("refreshes tokens through the relay", async () => {
    const a = await register();
    route("POST", /api\.etsy\.com\/v3\/public\/oauth\/token/, ({ body }) =>
      new URLSearchParams(body).get("refresh_token") === "dead"
        ? new Response('{"error":"invalid_grant"}', { status: 400 })
        : { access_token: "1.new", refresh_token: "1.newer", expires_in: 3600 },
    );
    const refresh = (refreshToken) =>
      relay("/studio/oauth/etsy/refresh", authed(a.token, { method: "POST", body: JSON.stringify({ refreshToken }) }));
    const ok = await refresh("1.old");
    expect((await ok.json()).tokens.accessToken).toBe("1.new");
    expect((await refresh("dead")).status).toBe(401);
  });
});

describe("eBay", () => {
  it("signs in, subscribes the seller to sales, and routes their orders to their device", async () => {
    const a = await register();
    const b = await register();
    const start = await connectEbay(a.token, "seller-a");
    const authorize = new URL(start.authorizeUrl);
    expect(authorize.origin).toBe("https://auth.ebay.com");
    expect(authorize.searchParams.get("redirect_uri")).toBe("test-runame");
    expect(authorize.searchParams.get("scope")).toContain("sell.fulfillment.readonly");

    const destination = calls.find((c) => c.method === "POST" && c.url.endsWith("/destination"));
    expect(JSON.parse(destination.body).deliveryConfig).toEqual({
      endpoint: `${ORIGIN}/ebay`,
      verificationToken: "test-verification".padEnd(40, "x"),
    });
    const subscription = calls.find((c) => c.method === "POST" && c.url.endsWith("/subscription"));
    expect(subscription.headers.get("authorization")).toBe("Bearer ebay-user-token");
    expect(JSON.parse(subscription.body)).toMatchObject({ topicId: "ORDER_CONFIRMATION", destinationId: "dest-1" });

    const result = await (await relay(`/studio/oauth/result?state=${start.state}`, authed(a.token))).json();
    expect(result).toMatchObject({ marketplace: "ebay", accountId: "seller-a", username: "name-seller-a", warnings: [] });
    expect(result.tokens.refreshToken).toBe("ebay-refresh");

    const order = {
      metadata: { topic: "ORDER_CONFIRMATION", schemaVersion: "1.0", deprecated: false },
      notification: {
        notificationId: "n-order-1",
        eventDate: "2026-10-24T01:00:00.000Z",
        publishDate: "2026-10-24T01:00:01.000Z",
        publishAttemptCount: 1,
        data: {
          user: { userId: "seller-a", username: "name-seller-a" },
          order: { orderId: "12-345", orderLineItems: [{ orderLineItemId: "li-1", listingId: "1234567890", quantity: 1 }] },
        },
      },
    };
    expect((await postEbayNotification(order)).status).toBe(204);
    const [event] = await events(a.token);
    expect(event).toMatchObject({ source: "ebay", eventType: "ORDER_CONFIRMATION" });
    expect(event.ids).toMatchObject({ orderId: ["12-345"], listingId: ["1234567890"], orderLineItemId: ["li-1"], userId: ["seller-a"] });
    expect(JSON.stringify(event)).not.toContain("name-seller-a");
    expect(await events(b.token)).toEqual([]);
  });

  it("reports when sale notifications could not be turned on", async () => {
    const a = await register();
    await mockEbayBasics();
    route("GET", /apiz\.ebay\.com\/commerce\/identity\/v1\/user/, () => ({ userId: "seller-w", username: "w" }));
    route("GET", /notification\/v1\/destination/, () => new Response("{}", { status: 500 }));
    const start = await (await relay("/studio/oauth/ebay/start", authed(a.token, { method: "POST" }))).json();
    expect((await relay(`/oauth/ebay/callback?code=abc&state=${start.state}`)).status).toBe(200);
    const result = await (await relay(`/studio/oauth/result?state=${start.state}`, authed(a.token))).json();
    expect(result.warnings).toHaveLength(1);
  });

  it("drops notifications for unlinked sellers and unlinks on account deletion", async () => {
    const a = await register();
    await connectEbay(a.token, "seller-d");
    const deletion = (id, userId) => ({
      metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
      notification: { notificationId: id, data: { username: "x", userId, eiasToken: "x" } },
    });
    expect((await postEbayNotification(deletion("n-del-0", "stranger"))).status).toBe(204);
    expect(await events(a.token)).toEqual([]);
    expect((await postEbayNotification(deletion("n-del-1", "seller-d"))).status).toBe(204);
    const [event] = await events(a.token);
    expect(event).toMatchObject({ eventType: "MARKETPLACE_ACCOUNT_DELETION", ids: { userId: ["seller-d"] } });
    const accounts = await (await relay("/studio/accounts", authed(a.token))).json();
    expect(accounts.accounts).toEqual([]);
  });

  it("rejects notifications that fail the signature check", async () => {
    await mockEbayBasics();
    const header = btoa(JSON.stringify({ alg: "ecdsa", kid: "kid-1", signature: btoa("junk"), digest: "SHA1" }));
    const response = await relay("/ebay", { method: "POST", body: "{}", headers: { "x-ebay-signature": header } });
    expect(response.status).toBe(412);
  });

  it("asks the seller to sign in again when a refresh token is dead", async () => {
    const a = await register();
    await mockEbayBasics();
    const response = await relay("/studio/oauth/ebay/refresh", authed(a.token, { method: "POST", body: JSON.stringify({ refreshToken: "dead" }) }));
    expect(response.status).toBe(401);
  });
});
