import { env, exports } from "cloudflare:workers";
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
    calls.push({ method: request.method, url: request.url, headers: request.headers, body, signal: init.signal });
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

// eBay key IDs are UUIDs, as in eBay's own SDK fixtures.
const EBAY_KID = "9936261a-7d7b-4621-a0f1-96ccb428af49";
const ebayHeader = (kid, signature) => btoa(JSON.stringify({ alg: "ecdsa", kid, signature, digest: "SHA1" }));
const now = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();

async function postEbayNotification(payload, kid = EBAY_KID) {
  const { pair } = await ebaySigner();
  const body = JSON.stringify(payload);
  const raw = new Uint8Array(
    await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-1" }, pair.privateKey, new TextEncoder().encode(body)),
  );
  return relay("/ebay", { method: "POST", body, headers: { "x-ebay-signature": ebayHeader(kid, bytesToBase64(rawToDer(raw))) } });
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
  route("GET", new RegExp(`notification/v1/public_key/${EBAY_KID}$`), () => ({ algorithm: "ECDSA", digest: "SHA1", key: pem }));
}

const returnTo = (marketplace) => `http://127.0.0.1:4318/api/marketplace-accounts/${marketplace}/finish`;

async function startSignIn(token, marketplace, { query = "", to = returnTo(marketplace) } = {}) {
  const params = new URLSearchParams(query);
  params.set("returnTo", to);
  const response = await relay(`/studio/oauth/${marketplace}/start?${params}`, authed(token, { method: "POST" }));
  expect(response.status).toBe(200);
  return response.json();
}

// The seller's browser approves; the relay sends it back to Anteroom with a one-time claim.
async function approve(marketplace, state, code) {
  const callback = await relay(`/oauth/${marketplace}/callback?code=${code}&state=${state}`, { redirect: "manual" });
  expect(callback.status).toBe(303);
  const location = new URL(callback.headers.get("location"));
  expect(location.searchParams.get("state")).toBe(state);
  expect(location.searchParams.get("marketplace")).toBe(marketplace);
  return { location, claim: location.searchParams.get("claim") };
}

const result = (token, state, claim) =>
  relay(`/studio/oauth/result?${new URLSearchParams(claim === undefined ? { state } : { state, claim })}`, authed(token));

function mockEbaySignIn(userId) {
  route("GET", /apiz\.ebay\.com\/commerce\/identity\/v1\/user/, () => ({ userId, username: `name-${userId}` }));
  route("GET", /notification\/v1\/destination/, () => ({ destinations: [] }));
  route("POST", /notification\/v1\/destination$/, () =>
    new Response(null, { status: 201, headers: { location: "https://api.ebay.com/commerce/notification/v1/destination/dest-1" } }),
  );
  route("GET", /notification\/v1\/subscription/, () => ({ subscriptions: [] }));
  route("POST", /notification\/v1\/subscription$/, () => new Response(null, { status: 201 }));
}

async function connectEbay(token, userId = "seller-1") {
  await mockEbayBasics();
  mockEbaySignIn(userId);
  const start = await startSignIn(token, "ebay");
  const { claim } = await approve("ebay", start.state, "abc");
  const done = await result(token, start.state, claim);
  expect(done.status).toBe(200);
  return { ...start, result: await done.json() };
}

function mockEtsySignIn(userId, shopId) {
  route("POST", /api\.etsy\.com\/v3\/public\/oauth\/token/, () => ({
    access_token: `${userId}.access`,
    refresh_token: `${userId}.refresh`,
    expires_in: 3600,
  }));
  route("GET", new RegExp(`api\\.etsy\\.com/v3/application/users/${userId}/shops`), () => ({ shop_id: Number(shopId), shop_name: `Shop${shopId}` }));
}

async function connectEtsy(token, { userId = "777", shopId = "42" } = {}) {
  mockEtsySignIn(userId, shopId);
  const start = await startSignIn(token, "etsy");
  const { claim } = await approve("etsy", start.state, "etsy-code");
  const done = await result(token, start.state, claim);
  expect(done.status).toBe(200);
  return { ...start, result: await done.json() };
}

// --- Tests -------------------------------------------------------------------

describe("devices", () => {
  it.each([true, false])("honors the registration limiter decision: %s", async (success) => {
    // The vendor's counter window can roll over between requests. Test our
    // handling of its decision without depending on the real wall clock.
    const limit = vi.spyOn(env.REGISTRATION_LIMITER, "limit").mockResolvedValue({ success });
    const response = await relay("/devices", { method: "POST", headers: { "cf-connecting-ip": "10.9.9.9" } });
    expect(response.status).toBe(success ? 201 : 429);
    expect(limit).toHaveBeenCalledTimes(1);
    expect(limit).toHaveBeenCalledWith({ key: "10.9.9.9" });
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

describe("sign-in", () => {
  it("only returns to Anteroom's own addresses", async () => {
    const a = await register();
    await mockEbayBasics();
    const start = (to) =>
      relay(`/studio/oauth/ebay/start${to === undefined ? "" : `?returnTo=${encodeURIComponent(to)}`}`, authed(a.token, { method: "POST" }));
    for (const to of [
      undefined,
      "https://evil.example/",
      "https://myanteroom.com.evil.example/",
      "https://myanteroom.com/elsewhere",
      "http://myanteroom.com/",
      "http://127.0.0.1:4318/api/marketplace-accounts/etsy/finish",
      "http://127.0.0.1:4318/api/settings",
      "http://192.168.1.20:4318/api/marketplace-accounts/ebay/finish",
      "http://user@127.0.0.1:4318/api/marketplace-accounts/ebay/finish",
      "anteroom://login-callback",
      "javascript:alert(1)",
    ]) {
      expect((await start(to)).status, String(to)).toBe(400);
    }
    for (const to of [
      "http://127.0.0.1:4318/api/marketplace-accounts/ebay/finish",
      "http://localhost:5173/api/marketplace-accounts/ebay/finish",
      "https://myanteroom.com/",
      "anteroom://marketplace-signin",
    ]) {
      expect((await start(to)).status, to).toBe(200);
    }
  });

  it("returns the approving browser to the address the device asked for", async () => {
    const a = await register();
    for (const to of ["https://myanteroom.com/", "anteroom://marketplace-signin"]) {
      mockEtsySignIn("790", "70");
      const start = await startSignIn(a.token, "etsy", { to });
      const { location } = await approve("etsy", start.state, "etsy-code");
      expect(`${location.protocol}//${location.host}${location.pathname}`).toBe(to);
    }
  });

  it("gives a device nothing for a sign-in someone else approved until Anteroom claims it", async () => {
    const owner = await register();
    const attacker = await register();
    await connectEbay(owner.token, "victim");

    // The attacker starts a sign-in and gets the victim to approve it.
    const start = await startSignIn(attacker.token, "ebay");
    const { claim } = await approve("ebay", start.state, "abc");

    // Without the claim, which only the approving browser saw, there is nothing to collect.
    const waiting = await result(attacker.token, start.state);
    expect(waiting.status).toBe(202);
    expect(await waiting.text()).not.toContain("ebay-user-token");
    const guessed = await result(attacker.token, start.state, "guess");
    expect(guessed.status).toBe(403);
    expect(await guessed.text()).not.toContain("ebay-user-token");

    // The victim's account stays linked to the victim's device.
    const accounts = async (token) => (await (await relay("/studio/accounts", authed(token))).json()).accounts;
    expect(await accounts(attacker.token)).toEqual([]);
    expect((await accounts(owner.token)).map((x) => x.accountId)).toEqual(["victim"]);

    // The claim is single use and links the account only when redeemed.
    expect(claim).toMatch(/^[A-Za-z0-9_-]{32,}$/);
  });

  it("reports a cancelled sign-in to the device without a redirect", async () => {
    const a = await register();
    await mockEbayBasics();
    const start = await startSignIn(a.token, "ebay");
    const page = await relay(`/oauth/ebay/callback?state=${start.state}&error=access_denied`, { redirect: "manual" });
    expect(page.status).toBe(400);
    const done = await result(a.token, start.state);
    expect(done.status).toBe(200);
    expect(await done.json()).toMatchObject({ status: "done", marketplace: "ebay", error: "access_denied" });
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

    const body = start.result;
    expect(body).toMatchObject({ status: "done", marketplace: "etsy", accountId: "42", shopName: "Shop42" });
    expect(body.tokens).toMatchObject({ accessToken: "777.access", refreshToken: "777.refresh" });
    expect((await result(a.token, start.state)).status).toBe(404);

    const accounts = await (await relay("/studio/accounts", authed(a.token))).json();
    expect(accounts.accounts).toEqual([expect.objectContaining({ marketplace: "etsy", accountId: "42", label: "Shop42" })]);
  });

  it("does not hand one device's sign-in to another, even with the claim", async () => {
    const a = await register();
    const b = await register();
    mockEtsySignIn("778", "43");
    const start = await startSignIn(a.token, "etsy");
    const { claim } = await approve("etsy", start.state, "etsy-code");
    expect((await result(b.token, start.state, claim)).status).toBe(404);
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

  it("refreshes tokens only for the device's own Etsy account", async () => {
    const a = await register();
    const b = await register();
    await connectEtsy(a.token, { userId: "1", shopId: "60" });
    route("POST", /api\.etsy\.com\/v3\/public\/oauth\/token/, ({ body }) => {
      const refreshToken = new URLSearchParams(body).get("refresh_token");
      if (refreshToken === "1.dead") return new Response('{"error":"invalid_grant"}', { status: 400 });
      if (refreshToken === "1.other") return { access_token: "2.new", refresh_token: "2.newer", expires_in: 3600 };
      return { access_token: "1.new", refresh_token: "1.newer", expires_in: 3600 };
    });
    const refresh = (token, refreshToken) =>
      relay("/studio/oauth/etsy/refresh", authed(token, { method: "POST", body: JSON.stringify({ refreshToken }) }));
    const ok = await refresh(a.token, "1.old");
    expect(ok.status).toBe(200);
    expect((await ok.json()).tokens).toMatchObject({ accessToken: "1.new", refreshToken: "1.newer" });
    expect((await refresh(a.token, "1.dead")).status).toBe(401);

    // Etsy rotates refresh tokens, so another device's request must not spend one.
    const exchanges = () => calls.filter((c) => c.url.includes("/v3/public/oauth/token")).length;
    const before = exchanges();
    const stranger = await refresh(b.token, "1.old");
    expect(stranger.status).toBe(401);
    expect(await stranger.text()).not.toContain("1.new");
    expect((await refresh(a.token, "no-user-prefix")).status).toBe(401);
    expect(exchanges()).toBe(before);

    // A token that turns out to belong to someone else is withheld.
    const swapped = await refresh(a.token, "1.other");
    expect(swapped.status).toBe(401);
    expect(await swapped.text()).not.toContain("2.new");
  });
});

describe("eBay", () => {
  it("grants feature groups explicitly and brokers public reads only for authenticated devices", async () => {
    const device = await register();
    const start = await startSignIn(device.token, "ebay", { query: "features=messages,finances" });
    const scope = new URL(start.authorizeUrl).searchParams.get("scope");
    expect(scope).toContain("commerce.message");
    expect(scope).toContain("/sell.finances");
    expect(scope).not.toContain("sell.finances.earnings.read");
    expect(scope).not.toContain("sell.marketing");
    const madeUp = `/studio/oauth/ebay/start?features=made-up&returnTo=${encodeURIComponent(returnTo("ebay"))}`;
    expect((await relay(madeUp, authed(device.token, { method: "POST" }))).status).toBe(400);
    const research = (operation, params = {}) => relay("/studio/ebay/research", authed(device.token, { method: "POST", body: JSON.stringify({ operation, params }) }));
    expect((await relay("/studio/ebay/research", { method: "POST" })).status).toBe(401);
    expect((await research("search", { q: "coat" })).status).toBe(403);
    await connectEbay(device.token, "seller-public-reads");
    route("GET", /buy\/browse\/v1\/item_summary\/search/, () => ({ total: 0, itemSummaries: [] }));
    const result = await research("search", { q: "coat" });
    expect(result.status).toBe(200);
    expect(result.headers.get("cache-control")).toBe("no-store");
    expect(await result.text()).not.toContain("app-token");
    expect((await relay("/studio/ebay/app-token", authed(device.token))).status).toBe(404);
    expect((await research("/commerce/notification/v1/destination")).status).toBe(400);
    expect((await research("search", { path: "/commerce/notification/v1/destination" })).status).toBe(400);
    route("GET", /ad_report_metadata\/CAMPAIGN_PERFORMANCE_SUMMARY_REPORT/, () => ({ dimensionMetadata: [], metricMetadata: [] }));
    const metadata = await research("ad_metadata");
    expect(metadata.status).toBe(200);
    expect(await metadata.text()).not.toContain("app-token");
    expect((await research("ad_metadata", { path: "/commerce/notification/v1/destination" })).status).toBe(400);
    expect(calls.filter((call) => call.url.includes("/destination") && call.method === "PUT")).toHaveLength(0);
  });

  it("signs in, subscribes the seller to sales, and routes their orders to their device", async () => {
    const a = await register();
    const b = await register();
    const start = await connectEbay(a.token, "seller-a");
    const authorize = new URL(start.authorizeUrl);
    expect(authorize.origin).toBe("https://auth.ebay.com");
    expect(authorize.searchParams.get("redirect_uri")).toBe("test-runame");
    expect(authorize.searchParams.get("scope")).toContain("sell.fulfillment.readonly");
    expect(authorize.searchParams.get("scope")).toContain("sell.inventory");

    const destination = calls.find((c) => c.method === "POST" && c.url.endsWith("/destination"));
    expect(JSON.parse(destination.body).deliveryConfig).toEqual({
      endpoint: `${ORIGIN}/ebay`,
      verificationToken: "test-verification".padEnd(40, "x"),
    });
    const subscription = calls.find((c) => c.method === "POST" && c.url.endsWith("/subscription"));
    expect(subscription.headers.get("authorization")).toBe("Bearer ebay-user-token");
    expect(JSON.parse(subscription.body)).toMatchObject({ topicId: "ORDER_CONFIRMATION", destinationId: "dest-1" });

    expect(start.result).toMatchObject({ marketplace: "ebay", accountId: "seller-a", username: "name-seller-a", warnings: [] });
    expect(start.result.tokens.refreshToken).toBe("ebay-refresh");

    const order = {
      metadata: { topic: "ORDER_CONFIRMATION", schemaVersion: "1.0", deprecated: false },
      notification: {
        notificationId: "n-order-1",
        eventDate: now(-1000),
        publishDate: now(),
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
    const start = await startSignIn(a.token, "ebay");
    const { claim } = await approve("ebay", start.state, "abc");
    expect((await (await result(a.token, start.state, claim)).json()).warnings).toHaveLength(1);
  });

  it("drops notifications for unlinked sellers and unlinks on account deletion", async () => {
    const a = await register();
    await connectEbay(a.token, "seller-d");
    const deletion = (id, userId) => ({
      metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
      notification: { notificationId: id, eventDate: now(), publishDate: now(), data: { username: "x", userId, eiasToken: "x" } },
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
    const response = await relay("/ebay", { method: "POST", body: "{}", headers: { "x-ebay-signature": ebayHeader(EBAY_KID, btoa("junk")) } });
    expect(response.status).toBe(412);
  });

  it("refreshes tokens only for the device's own eBay account", async () => {
    const a = await register();
    const b = await register();
    await connectEbay(a.token, "seller-r");
    const refresh = (token) =>
      relay("/studio/oauth/ebay/refresh", authed(token, { method: "POST", body: JSON.stringify({ refreshToken: "ebay-refresh" }) }));
    const ok = await refresh(a.token);
    expect(ok.status).toBe(200);
    expect((await ok.json()).tokens.accessToken).toBe("ebay-user-token-2");
    const stranger = await refresh(b.token);
    expect(stranger.status).toBe(401);
    expect(await stranger.text()).not.toContain("ebay-user-token-2");
  });

  it("asks the seller to sign in again when a refresh token is dead", async () => {
    const a = await register();
    await mockEbayBasics();
    const response = await relay("/studio/oauth/ebay/refresh", authed(a.token, { method: "POST", body: JSON.stringify({ refreshToken: "dead" }) }));
    expect(response.status).toBe(401);
  });
});

describe("eBay public keys", () => {
  const keyFetches = () => calls.filter((call) => call.url.includes("/public_key/"));
  const order = (id) => ({
    metadata: { topic: "ORDER_CONFIRMATION" },
    notification: { notificationId: id, eventDate: now(), publishDate: now(), data: { user: { userId: "nobody" } } },
  });

  it.each(["kid-1", "../destination", "9936261a-7d7b-4621-a0f1-96ccb428af4", `${EBAY_KID}x`])(
    "refuses a key ID that is not a UUID without asking eBay: %s",
    async (kid) => {
      await mockEbayBasics();
      expect((await postEbayNotification(order("n-kid"), kid)).status).toBe(412);
      expect(keyFetches()).toHaveLength(0);
    },
  );

  it("remembers a key eBay does not know instead of asking again", async () => {
    await mockEbayBasics();
    const unknown = "00000000-0000-4000-8000-000000000001";
    route("GET", new RegExp(`public_key/${unknown}$`), () => new Response("{}", { status: 404 }));
    expect((await postEbayNotification(order("n-u1"), unknown)).status).toBe(500);
    expect((await postEbayNotification(order("n-u2"), unknown)).status).toBe(500);
    expect(keyFetches()).toHaveLength(1);
  });

  it("stops fetching keys when the key limiter says so", async () => {
    await mockEbayBasics();
    vi.spyOn(env.EBAY_KEY_LIMITER, "limit").mockResolvedValue({ success: false });
    const response = await postEbayNotification(order("n-limited"), "00000000-0000-4000-8000-000000000002");
    expect(response.status).toBe(429);
    expect(keyFetches()).toHaveLength(0);
  });
});

describe("replays", () => {
  it("refuses eBay notifications published more than a day ago", async () => {
    const a = await register();
    await connectEbay(a.token, "seller-old");
    const stale = {
      metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
      notification: { notificationId: "n-old", eventDate: now(-25 * 3600e3), publishDate: now(-25 * 3600e3), data: { userId: "seller-old" } },
    };
    expect((await postEbayNotification(stale)).status).toBe(412);
    const undated = { ...stale, notification: { notificationId: "n-undated", data: { userId: "seller-old" } } };
    expect((await postEbayNotification(undated)).status).toBe(400);
    expect((await (await relay("/studio/accounts", authed(a.token))).json()).accounts).toHaveLength(1);
    expect(await events(a.token)).toEqual([]);
  });

  it("does not accept an acknowledged eBay notification again", async () => {
    const a = await register();
    await connectEbay(a.token, "seller-replay");
    const deletion = {
      metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
      notification: { notificationId: "n-replay", eventDate: now(), publishDate: now(), data: { userId: "seller-replay" } },
    };
    expect((await postEbayNotification(deletion)).status).toBe(204);
    const [event] = await events(a.token);
    await relay("/studio/ack", authed(a.token, { method: "POST", body: JSON.stringify({ through: event.seq }) }));
    await connectEbay(a.token, "seller-replay");
    expect((await postEbayNotification(deletion)).status).toBe(204);
    expect(await events(a.token)).toEqual([]);
    expect((await (await relay("/studio/accounts", authed(a.token))).json()).accounts).toHaveLength(1);
  });

  it("does not accept an acknowledged Etsy webhook again", async () => {
    const a = await register();
    await connectEtsy(a.token, { userId: "790", shopId: "60" });
    const paid = { event_type: "order.paid", shop_id: 60, resource_url: "https://api.etsy.com/v3/application/shops/60/receipts/960" };
    expect((await postEtsyWebhook("msg_replay", paid)).status).toBe(200);
    const [event] = await events(a.token);
    await relay("/studio/ack", authed(a.token, { method: "POST", body: JSON.stringify({ through: event.seq }) }));
    expect((await postEtsyWebhook("msg_replay", paid)).status).toBe(200);
    expect(await events(a.token)).toEqual([]);
  });
});

describe("request bodies", () => {
  // A chunked body with no Content-Length: count how much the relay reads.
  function endless() {
    const stats = { read: 0 };
    const chunk = new Uint8Array(16 * 1024).fill(0x20);
    const body = new ReadableStream({
      pull(controller) {
        stats.read += chunk.length;
        if (stats.read > 32 * 1024 * 1024) controller.close();
        else controller.enqueue(chunk);
      },
    });
    return { body, stats };
  }

  it("stops reading a webhook body once it passes the limit", async () => {
    const { body, stats } = endless();
    const response = await relay("/etsy", { method: "POST", body, duplex: "half" });
    expect(response.status).toBe(413);
    expect(stats.read).toBeLessThan(1024 * 1024);
  });

  it("stops reading a research body once it passes the limit", async () => {
    const device = await register();
    await connectEbay(device.token, "seller-big");
    const { body, stats } = endless();
    const response = await relay("/studio/ebay/research", authed(device.token, { method: "POST", body, duplex: "half" }));
    expect(response.status).toBe(413);
    expect(stats.read).toBeLessThan(12 * 1024 * 1024);
  });
});

describe("deadlines", () => {
  it("gives every call to eBay and Etsy a deadline", async () => {
    const device = await register();
    await connectEbay(device.token, "seller-deadline");
    await connectEtsy(device.token, { userId: "791", shopId: "61" });
    route("GET", /api\.etsy\.com\/v3\/application\/shops\/61$/, () => ({ shop_id: 61 }));
    await relay("/studio/etsy/v3/application/shops/61", authed(device.token, { headers: { "x-etsy-authorization": "Bearer 791.access" } }));
    expect(calls.length).toBeGreaterThan(5);
    for (const call of calls) expect(call.signal, call.url).toBeInstanceOf(AbortSignal);
  });

  it("answers 504 when eBay research or the Etsy API times out", async () => {
    const device = await register();
    await connectEbay(device.token, "seller-slow");
    await connectEtsy(device.token, { userId: "792", shopId: "62" });
    const timeout = () => {
      throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
    };
    route("GET", /buy\/browse\/v1\/item_summary\/search/, timeout);
    route("GET", /api\.etsy\.com\/v3\/application\/shops\/62$/, timeout);
    const research = await relay("/studio/ebay/research", authed(device.token, { method: "POST", body: JSON.stringify({ operation: "search", params: { q: "x" } }) }));
    expect(research.status).toBe(504);
    const etsy = await relay("/studio/etsy/v3/application/shops/62", authed(device.token, { headers: { "x-etsy-authorization": "Bearer 792.access" } }));
    expect(etsy.status).toBe(504);
  });
});
