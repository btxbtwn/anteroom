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
    calls.push({ method: request.method, url: request.url, headers: request.headers, body, signal: init.signal, redirect: init.redirect });
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

  it("lets the hosted server bypass the per-IP registration limiter only with the shared secret", async () => {
    const limit = vi.spyOn(env.REGISTRATION_LIMITER, "limit").mockResolvedValue({ success: false });
    const bad = await relay("/devices", {
      method: "POST",
      headers: { "cf-connecting-ip": "10.9.9.8", "x-anteroom-register-secret": "wrong" },
    });
    expect(bad.status).toBe(429);
    expect(limit).toHaveBeenCalledTimes(1);

    const ok = await relay("/devices", {
      method: "POST",
      headers: { "cf-connecting-ip": "10.9.9.8", "x-anteroom-register-secret": "test-hosted-register-secret" },
    });
    expect(ok.status).toBe(201);
    expect(limit).toHaveBeenCalledTimes(1);
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

  it("enforces per-device Etsy proxy rate limits so one seller cannot starve others", async () => {
    const a = await register();
    await connectEtsy(a.token, { userId: "783", shopId: "53" });
    const limit = vi.spyOn(env.ETSY_PROXY_LIMITER, "limit").mockResolvedValue({ success: false });
    const refused = await relay(
      "/studio/etsy/v3/application/shops/53/receipts?limit=5",
      authed(a.token, { headers: { "x-etsy-authorization": "Bearer 783.access" } }),
    );
    expect(refused.status).toBe(429);
    expect(refused.headers.get("retry-after")).toBe("5");
    expect(refused.headers.get("x-anteroom-etsy-throttle-source")).toBe("relay");
    expect(limit).toHaveBeenCalledWith({ key: a.deviceId });
    expect(calls.some((c) => c.url.includes("/shops/53/receipts"))).toBe(false);
  });

  it("marks upstream Etsy throttling and forwards its quota and retry headers", async () => {
    const a = await register();
    await connectEtsy(a.token, { userId: "784", shopId: "54" });
    const info = vi.spyOn(console, "info");
    route("GET", /api\.etsy\.com\/v3\/application\/shops\/54\/shipping-profiles/, () =>
      new Response("Too many requests", { status: 429, headers: {
        "retry-after": "17", "x-limit-per-second": "10", "x-remaining-this-secon": "0",
        "x-limit-per-day": "10000", "x-remaining-today": "9821",
        "x-anteroom-etsy-throttle-source": "relay",
      } }),
    );
    const reply = await relay(
      "/studio/etsy/v3/application/shops/54/shipping-profiles",
      authed(a.token, { headers: { "x-etsy-authorization": "Bearer 784.access" } }),
    );
    expect(reply.status).toBe(429);
    expect(reply.headers.get("x-anteroom-etsy-throttle-source")).toBe("etsy");
    expect(reply.headers.get("retry-after")).toBe("17");
    expect(reply.headers.get("x-limit-per-second")).toBe("10");
    expect(reply.headers.get("x-remaining-this-secon")).toBe("0");
    expect(reply.headers.get("x-limit-per-day")).toBe("10000");
    expect(reply.headers.get("x-remaining-today")).toBe("9821");
    expect(info).toHaveBeenCalledWith({
      event: "etsy_api_quota", source: "etsy", status: 429, "retry-after": 17,
      "x-limit-per-second": 10, "x-remaining-this-secon": 0, "x-limit-per-day": 10000,
      "x-remaining-today": 9821,
    });
  });

  it("samples successful Etsy quota headers without logging untrusted values", async () => {
    const a = await register();
    await connectEtsy(a.token, { userId: "785", shopId: "55" });
    const info = vi.spyOn(console, "info");
    route("GET", /api\.etsy\.com\/v3\/application\/shops\/55\/shipping-profiles/, () =>
      Response.json({ results: [] }, { headers: {
        "x-limit-per-day": "10000", "x-remaining-today": "9800",
        "x-limit-per-second": "arbitrary upstream text",
        "x-anteroom-etsy-throttle-source": "relay",
      } }),
    );
    const load = () => relay("/studio/etsy/v3/application/shops/55/shipping-profiles",
      authed(a.token, { headers: { "x-etsy-authorization": "Bearer 785.access" } }));
    const first = await load();
    expect(first.status).toBe(200);
    expect(first.headers.has("x-anteroom-etsy-throttle-source")).toBe(false);
    expect((await load()).status).toBe(200);
    expect(info.mock.calls.filter(([log]) => log?.event === "etsy_api_quota")).toEqual([[{
      event: "etsy_api_quota", source: "etsy", status: 200,
      "x-limit-per-day": 10000, "x-remaining-today": 9800,
    }]]);
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

  it("revokes refresh and API proxy access when the Etsy shop is unlinked", async () => {
    const a = await register();
    await connectEtsy(a.token, { userId: "785", shopId: "55" });
    route("GET", /api\.etsy\.com\/v3\/application\/shops\/55\/receipts/, () => ({ count: 0, results: [] }));
    route("POST", /api\.etsy\.com\/v3\/public\/oauth\/token/, () => ({
      access_token: "785.new",
      refresh_token: "785.newer",
      expires_in: 3600,
    }));

    const proxy = () =>
      relay("/studio/etsy/v3/application/shops/55/receipts", authed(a.token, { headers: { "x-etsy-authorization": "Bearer 785.access" } }));
    const refresh = () =>
      relay("/studio/oauth/etsy/refresh", authed(a.token, { method: "POST", body: JSON.stringify({ refreshToken: "785.refresh" }) }));

    expect((await proxy()).status).toBe(200);
    expect((await refresh()).status).toBe(200);

    expect((await relay("/studio/accounts/etsy/55", authed(a.token, { method: "DELETE" }))).status).toBe(200);
    expect((await (await relay("/studio/accounts", authed(a.token))).json()).accounts).toEqual([]);

    const callsBefore = calls.length;
    expect((await refresh()).status).toBe(401);
    expect((await proxy()).status).toBe(403);
    expect(calls.length).toBe(callsBefore);
  });

  it("refuses upstream redirects from Etsy API with 502 and uses redirect: manual", async () => {
    const a = await register();
    await connectEtsy(a.token, { userId: "786", shopId: "56" });
    route("GET", /api\.etsy\.com\/v3\/application\/shops\/56\/receipts/, () =>
      new Response("redirecting", { status: 302, headers: { location: "https://evil.example/steal" } }),
    );
    const response = await relay(
      "/studio/etsy/v3/application/shops/56/receipts",
      authed(a.token, { headers: { "x-etsy-authorization": "Bearer 786.access" } }),
    );
    expect(response.status).toBe(502);
    expect(response.headers.get("location")).toBe(null);
    const upstream = calls.find((c) => c.url.includes("/shops/56/receipts"));
    expect(upstream.redirect).toBe("manual");
  });

  it("passes through 304 Not Modified with ETag on conditional Etsy API requests", async () => {
    const a = await register();
    await connectEtsy(a.token, { userId: "787", shopId: "57" });
    route("GET", /api\.etsy\.com\/v3\/application\/shops\/57\/receipts/, () =>
      new Response(null, { status: 304, headers: { etag: '"v1-etag"' } }),
    );
    const response = await relay(
      "/studio/etsy/v3/application/shops/57/receipts",
      authed(a.token, {
        headers: {
          "x-etsy-authorization": "Bearer 787.access",
          "if-none-match": '"v1-etag"',
        },
      }),
    );
    expect(response.status).toBe(304);
    expect(response.headers.get("etag")).toBe('"v1-etag"');
    expect(await response.text()).toBe("");
    const upstream = calls.find((c) => c.url.includes("/shops/57/receipts"));
    expect(upstream.redirect).toBe("manual");
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

  it("resolves and uses an allowlisted regional eBay taxonomy tree", async () => {
    const device = await register();
    await connectEbay(device.token, "seller-canadian-taxonomy");
    route("GET", /taxonomy\/v1\/get_default_category_tree_id/, () => ({ categoryTreeId: "2" }));
    route("GET", /taxonomy\/v1\/category_tree\/2\/get_item_aspects_for_category/, () => ({ aspects: [] }));
    const request = (body) => relay("/studio/ebay/research", authed(device.token, { method: "POST", body: JSON.stringify(body) }));
    const tree = await request({ operation: "category_tree", params: { marketplace_id: "EBAY_CA" } });
    expect(await tree.json()).toEqual({ categoryTreeId: "2" });
    const aspects = { operation: "regional_aspects", marketplace_id: "EBAY_CA", tree_id: "2", params: { category_id: "11483" } };
    expect((await request(aspects)).status).toBe(200);
    const taxonomy = calls.filter(call => call.url.includes("/commerce/taxonomy/"));
    expect(taxonomy).toHaveLength(2);
    expect(taxonomy.every(call => call.headers.get("accept-language") === "en-CA")).toBe(true);
    expect((await request({ ...aspects, tree_id: "2/../0" })).status).toBe(400);
    expect((await request({ ...aspects, marketplace_id: "EBAY_DE" })).status).toBe(400);
  });

  it("rate-limits eBay research per device before calling upstream eBay APIs", async () => {
    const device = await register();
    await connectEbay(device.token, "seller-research-limit");
    const limit = vi.spyOn(env.EBAY_RESEARCH_LIMITER, "limit").mockResolvedValue({ success: false });
    const before = calls.length;
    const response = await relay(
      "/studio/ebay/research",
      authed(device.token, { method: "POST", body: JSON.stringify({ operation: "search", params: { q: "coat" } }) }),
    );
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("60");
    expect(limit).toHaveBeenCalledWith({ key: device.deviceId });
    expect(calls.length).toBe(before);
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

  it("rejects notifications that fail the signature check, including malformed base64", async () => {
    await mockEbayBasics();
    const junk = await relay("/ebay", { method: "POST", body: "{}", headers: { "x-ebay-signature": ebayHeader(EBAY_KID, btoa("junk")) } });
    expect(junk.status).toBe(412);
    const badBase64 = await relay("/ebay", {
      method: "POST",
      body: "{}",
      headers: { "x-ebay-signature": ebayHeader(EBAY_KID, "not valid base64!!!") },
    });
    expect(badBase64.status).toBe(412);
  });

  it("refreshes tokens only for the device's own eBay account and blocks unlinked devices before calling eBay", async () => {
    const a = await register();
    const b = await register();
    const c = await register();
    await connectEbay(a.token, "seller-r");
    await connectEbay(c.token, "seller-c");
    // Ensure subsequent user lookup returns seller-r (the token owner).
    route("GET", /apiz\.ebay\.com\/commerce\/identity\/v1\/user/, () => ({ userId: "seller-r", username: "name-seller-r" }));
    const refresh = (token) =>
      relay("/studio/oauth/ebay/refresh", authed(token, { method: "POST", body: JSON.stringify({ refreshToken: "ebay-refresh" }) }));
    const ok = await refresh(a.token);
    expect(ok.status).toBe(200);
    expect((await ok.json()).tokens.accessToken).toBe("ebay-user-token-2");

    // Unlinked device b must be rejected without calling eBay's token endpoint.
    const tokenCalls = () => calls.filter((call) => call.url.includes("/identity/v1/oauth2/token")).length;
    const before = tokenCalls();
    const unlinked = await refresh(b.token);
    expect(unlinked.status).toBe(401);
    expect(await unlinked.text()).not.toContain("ebay-user-token-2");
    expect(tokenCalls()).toBe(before);

    // Linked device c cannot refresh seller-r's token either.
    const other = await refresh(c.token);
    expect(other.status).toBe(401);
    expect(await other.text()).not.toContain("ebay-user-token-2");
  });

  it("asks the seller to sign in again when a refresh token is dead", async () => {
    const a = await register();
    await connectEbay(a.token, "seller-dead");
    const before = calls.filter((call) => call.url.includes("/identity/v1/oauth2/token")).length;
    const response = await relay("/studio/oauth/ebay/refresh", authed(a.token, { method: "POST", body: JSON.stringify({ refreshToken: "dead" }) }));
    expect(response.status).toBe(401);
    expect(calls.filter((call) => call.url.includes("/identity/v1/oauth2/token")).length).toBe(before + 1);
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
  it("refuses eBay notifications published more than a day ago or far in the future", async () => {
    const a = await register();
    await connectEbay(a.token, "seller-old");
    const stale = {
      metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
      notification: { notificationId: "n-old", eventDate: now(-25 * 3600e3), publishDate: now(-25 * 3600e3), data: { userId: "seller-old" } },
    };
    expect((await postEbayNotification(stale)).status).toBe(412);
    const future = {
      metadata: { topic: "MARKETPLACE_ACCOUNT_DELETION" },
      notification: { notificationId: "n-future", eventDate: now(10 * 60e3), publishDate: now(10 * 60e3), data: { userId: "seller-old" } },
    };
    expect((await postEbayNotification(future)).status).toBe(412);
    const undated = { ...stale, notification: { notificationId: "n-undated", data: { userId: "seller-old" } } };
    expect((await postEbayNotification(undated)).status).toBe(400);
    expect((await (await relay("/studio/accounts", authed(a.token))).json()).accounts).toHaveLength(1);
    expect(await events(a.token)).toEqual([]);

    // Small clock skew within 5 minutes is tolerated.
    const slightSkew = {
      metadata: { topic: "ORDER_CONFIRMATION" },
      notification: {
        notificationId: "n-skew",
        eventDate: now(2 * 60e3),
        publishDate: now(2 * 60e3),
        data: { user: { userId: "seller-old" }, order: { orderId: "12-999" } },
      },
    };
    expect((await postEbayNotification(slightSkew)).status).toBe(204);
    expect(await events(a.token)).toHaveLength(1);
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

  it.each(["/studio/ack", "/studio/oauth/ebay/refresh", "/studio/oauth/etsy/refresh"])(
    "stops reading %s body once it passes the limit",
    async (path) => {
      const device = await register();
      const { body, stats } = endless();
      const response = await relay(path, authed(device.token, { method: "POST", body, duplex: "half" }));
      expect(response.status).toBe(413);
      expect(stats.read).toBeLessThan(1024 * 1024);
    },
  );
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
