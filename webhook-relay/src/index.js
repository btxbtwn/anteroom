import { Directory } from "./directory.js";
import {
  ebayAuthorizeUrl,
  ebayConfigured,
  ebayExchangeCode,
  ebayPublicKey,
  ebayRefresh,
  ebayUser,
  ensureEbayDestination,
  ensureEbaySubscriptions,
} from "./ebay.js";
import {
  etsyAuthorizeUrl,
  etsyCallbackUrl,
  etsyConfigured,
  etsyExchangeCode,
  etsyForward,
  etsyRefresh,
  etsyShop,
  etsyUserIdFromToken,
} from "./etsy.js";
import { extractEbayEvent, extractEtsyEvent } from "./extract.js";
import { Inbox } from "./inbox.js";
import { callbackPage, privacyPage } from "./pages.js";
import {
  ebayChallengeResponse,
  parseEbaySignatureHeader,
  pkceChallenge,
  randomToken,
  sha256Hex,
  verifyEbaySignature,
  verifyEtsySignature,
} from "./verify.js";

export { Directory, Inbox };

const MAX_BODY_BYTES = 64 * 1024;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

function directory(env) {
  return env.DIRECTORY.get(env.DIRECTORY.idFromName("directory"));
}

function inbox(env, deviceId) {
  return env.INBOX.get(env.INBOX.idFromName(`device:${deviceId}`));
}

function ebayEndpoint(env, url) {
  return env.EBAY_ENDPOINT_URL || `${url.origin}/ebay`;
}

async function readBody(request) {
  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > MAX_BODY_BYTES) return null;
  const text = await request.text();
  return new TextEncoder().encode(text).length > MAX_BODY_BYTES ? null : text;
}

function bearer(request) {
  const header = request.headers.get("authorization") ?? "";
  return header.startsWith("Bearer ") ? header.slice(7).trim() : "";
}

async function authenticate(request, env) {
  const token = bearer(request);
  if (!token) return null;
  return directory(env).deviceForToken(await sha256Hex(token));
}

// --- Marketplace webhooks -------------------------------------------------

async function handleEbayChallenge(env, url) {
  const challengeCode = url.searchParams.get("challenge_code");
  if (!challengeCode) return json({ error: "missing challenge_code" }, 400);
  if (!env.EBAY_VERIFICATION_TOKEN) return json({ error: "eBay is not configured" }, 503);
  const challengeResponse = await ebayChallengeResponse(
    challengeCode,
    env.EBAY_VERIFICATION_TOKEN,
    ebayEndpoint(env, url),
  );
  return json({ challengeResponse });
}

async function handleEbayNotification(request, env) {
  if (!ebayConfigured(env)) return json({ error: "eBay is not configured" }, 503);
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
  const userId = event.ids.userId?.[0];
  const dir = directory(env);
  const deviceId = userId ? await dir.deviceForAccount("ebay", userId) : null;
  if (event.eventType === "MARKETPLACE_ACCOUNT_DELETION" && userId) await dir.unlink("ebay", userId);
  if (!deviceId) {
    // Not one of our sellers (or eBay's portal test). Acknowledge so eBay stops retrying.
    console.log("[EBAY]", event.eventType, "for an unlinked account");
    return new Response(null, { status: 204 });
  }
  await inbox(env, deviceId).add(event);
  return new Response(null, { status: 204 });
}

async function handleEtsyWebhook(request, env) {
  if (!env.ETSY_WEBHOOK_SECRET) return json({ error: "Etsy webhooks are not configured" }, 503);
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
  const shopId = event.ids.shop_id?.[0];
  const deviceId = shopId ? await directory(env).deviceForAccount("etsy", shopId) : null;
  if (!deviceId) {
    console.log("[ETSY]", event.eventType, "for an unlinked shop");
    return json({ ok: true });
  }
  await inbox(env, deviceId).add(event);
  return json({ ok: true });
}

// --- Devices ----------------------------------------------------------------

async function handleRegister(request, env) {
  if (env.REGISTRATION_LIMITER) {
    const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
    const { success } = await env.REGISTRATION_LIMITER.limit({ key: ip });
    if (!success) return json({ error: "too many registrations, try again in a minute" }, 429);
  }
  const deviceId = crypto.randomUUID();
  const token = randomToken();
  await directory(env).createDevice(deviceId, await sha256Hex(token));
  return json({ deviceId, token }, 201);
}

// --- Sign-in ------------------------------------------------------------------

async function handleOAuthStart(env, deviceId, marketplace, url) {
  const state = randomToken(24);
  if (marketplace === "ebay") {
    if (!ebayConfigured(env) || !env.EBAY_RUNAME) return json({ error: "eBay sign-in is not configured" }, 503);
    await directory(env).startSession(state, deviceId, "ebay");
    return json({ state, authorizeUrl: ebayAuthorizeUrl(env, state) });
  }
  if (marketplace === "etsy") {
    if (!etsyConfigured(env)) return json({ error: "Etsy sign-in is not configured" }, 503);
    const verifier = randomToken(48);
    await directory(env).startSession(state, deviceId, "etsy", verifier);
    const authorizeUrl = etsyAuthorizeUrl(env, {
      state,
      challenge: await pkceChallenge(verifier),
      redirectUri: etsyCallbackUrl(url.origin),
    });
    return json({ state, authorizeUrl });
  }
  return json({ error: "unknown marketplace" }, 404);
}

function tokenSet(body) {
  return {
    accessToken: body.access_token,
    refreshToken: body.refresh_token ?? null,
    expiresIn: body.expires_in ?? null,
    refreshTokenExpiresIn: body.refresh_token_expires_in ?? null,
  };
}

async function completeEbay(env, url, code) {
  const tokens = await ebayExchangeCode(env, code);
  const user = await ebayUser(env, tokens.access_token);
  const warnings = [];
  try {
    const destinationId = await ensureEbayDestination(env, ebayEndpoint(env, url));
    await ensureEbaySubscriptions(env, tokens.access_token, destinationId);
  } catch (error) {
    console.error("[EBAY] subscription", error.message);
    warnings.push("Sale notifications could not be turned on yet; Anteroom will retry.");
  }
  return {
    accountId: user.userId,
    label: user.username,
    result: { marketplace: "ebay", accountId: user.userId, username: user.username, tokens: tokenSet(tokens), warnings },
  };
}

async function completeEtsy(env, url, code, verifier) {
  const tokens = await etsyExchangeCode(env, { code, verifier, redirectUri: etsyCallbackUrl(url.origin) });
  const shop = await etsyShop(env, tokens.access_token);
  return {
    accountId: shop.shopId,
    label: shop.shopName,
    userId: shop.userId,
    result: {
      marketplace: "etsy",
      accountId: shop.shopId,
      shopName: shop.shopName,
      userId: shop.userId,
      tokens: tokenSet(tokens),
      warnings: [],
    },
  };
}

async function handleOAuthCallback(env, marketplace, url) {
  const state = url.searchParams.get("state") ?? "";
  const dir = directory(env);
  const session = state ? await dir.session(state) : null;
  const name = marketplace === "ebay" ? "eBay" : "Etsy";
  if (!session || session.marketplace !== marketplace || session.done) {
    return callbackPage({ ok: false, title: "This sign-in link has expired", detail: `Start again from Anteroom's Connections settings.` }, 400);
  }
  const code = url.searchParams.get("code");
  if (!code) {
    const reason = url.searchParams.get("error_description") || url.searchParams.get("error") || "The sign-in was cancelled.";
    await dir.finishSession(state, { marketplace, error: reason });
    return callbackPage({ ok: false, title: `${name} was not connected`, detail: reason }, 400);
  }
  try {
    const done =
      marketplace === "ebay"
        ? await completeEbay(env, url, code)
        : await completeEtsy(env, url, code, session.verifier);
    await dir.link(marketplace, done.accountId, session.deviceId, done.label);
    if (done.userId) await dir.link("etsy-user", done.userId, session.deviceId, done.label);
    await dir.finishSession(state, done.result);
    return callbackPage({
      ok: true,
      title: `${name} is connected`,
      detail: `${done.label ? `${done.label} is` : "Your account is"} now linked to Anteroom. You can close this tab.`,
    });
  } catch (error) {
    console.error(`[${name.toUpperCase()}] sign-in`, error.message);
    await dir.finishSession(state, { marketplace, error: `${name} sign-in failed. Try again.` });
    return callbackPage({ ok: false, title: `${name} was not connected`, detail: "Something went wrong finishing the sign-in. Try again from Anteroom." }, 502);
  }
}

async function handleOAuthRefresh(request, env, marketplace) {
  const body = await request.json().catch(() => null);
  const refreshToken = typeof body?.refreshToken === "string" ? body.refreshToken : "";
  if (!refreshToken) return json({ error: "refreshToken is required" }, 400);
  try {
    if (marketplace === "ebay") {
      if (!ebayConfigured(env)) return json({ error: "eBay is not configured" }, 503);
      return json({ tokens: tokenSet(await ebayRefresh(env, refreshToken)) });
    }
    if (marketplace === "etsy") {
      if (!etsyConfigured(env)) return json({ error: "Etsy is not configured" }, 503);
      return json({ tokens: tokenSet(await etsyRefresh(env, refreshToken)) });
    }
  } catch (error) {
    // 400/401 from the marketplace means the seller has to sign in again.
    const status = error.status === 400 || error.status === 401 ? 401 : 502;
    return json({ error: status === 401 ? "sign in again" : "marketplace unavailable" }, status);
  }
  return json({ error: "unknown marketplace" }, 404);
}

async function handleEtsyProxy(request, env, deviceId, path) {
  if (!etsyConfigured(env)) return json({ error: "Etsy is not configured" }, 503);
  const header = request.headers.get("x-etsy-authorization") ?? "";
  const etsyToken = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  const etsyUser = etsyUserIdFromToken(etsyToken);
  if (!etsyUser) return json({ error: "X-Etsy-Authorization: Bearer <Etsy token> is required" }, 400);
  // Only accounts that signed in through this device may use the app key here.
  if ((await directory(env).deviceForAccount("etsy-user", etsyUser)) !== deviceId) {
    return json({ error: "this Etsy account is not linked to this device" }, 403);
  }
  if (!/^[A-Za-z0-9_\-/.]+$/.test(path) || path.includes("..")) return json({ error: "bad path" }, 400);
  return etsyForward(env, request, path, etsyToken);
}

// --- Studio ---------------------------------------------------------------

async function handleStudio(request, env, url, deviceId) {
  const box = inbox(env, deviceId);
  const { pathname } = url;
  if (pathname === "/studio/stream") {
    if (request.headers.get("upgrade") !== "websocket") return json({ error: "expected websocket" }, 426);
    return box.fetch(request);
  }
  if (pathname === "/studio/events" && request.method === "GET") {
    const after = Number(url.searchParams.get("after") ?? 0) || 0;
    const limit = Number(url.searchParams.get("limit") ?? 200) || 200;
    return json({ events: await box.list(after, limit) });
  }
  if (pathname === "/studio/ack" && request.method === "POST") {
    const body = await request.json().catch(() => null);
    if (!Number.isInteger(body?.through)) return json({ error: "through must be an integer" }, 400);
    return json({ pending: await box.ack(body.through) });
  }
  if (pathname === "/studio/accounts" && request.method === "GET") {
    const accounts = (await directory(env).accountsForDevice(deviceId)).filter((a) => a.marketplace !== "etsy-user");
    return json({ accounts });
  }
  const unlink = /^\/studio\/accounts\/(ebay|etsy)\/([A-Za-z0-9._:-]{1,128})$/.exec(pathname);
  if (unlink && request.method === "DELETE") {
    await directory(env).unlink(unlink[1], unlink[2], deviceId);
    return json({ ok: true });
  }
  if (pathname === "/studio/device" && request.method === "DELETE") {
    await directory(env).deleteDevice(deviceId);
    await box.clear();
    return json({ ok: true });
  }
  const start = /^\/studio\/oauth\/(ebay|etsy)\/start$/.exec(pathname);
  if (start && request.method === "POST") return handleOAuthStart(env, deviceId, start[1], url);
  if (pathname === "/studio/oauth/result" && request.method === "GET") {
    const state = url.searchParams.get("state") ?? "";
    const outcome = await directory(env).takeResult(state, deviceId);
    if (outcome.status === "pending") return json({ status: "pending" }, 202);
    if (outcome.status === "unknown") return json({ status: "unknown" }, 404);
    return json({ status: "done", ...outcome.result });
  }
  const refresh = /^\/studio\/oauth\/(ebay|etsy)\/refresh$/.exec(pathname);
  if (refresh && request.method === "POST") return handleOAuthRefresh(request, env, refresh[1]);
  if (pathname.startsWith("/studio/etsy/v3/application/")) {
    return handleEtsyProxy(request, env, deviceId, pathname.slice("/studio/etsy/v3/application/".length));
  }
  return json({ error: "not found" }, 404);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const { pathname } = url;
    if (pathname === "/" || pathname === "/health") return json({ ok: true });
    if (pathname === "/privacy") return privacyPage();
    if (pathname === "/ebay") {
      if (request.method === "GET") return handleEbayChallenge(env, url);
      if (request.method === "POST") return handleEbayNotification(request, env);
    }
    if (pathname === "/etsy" && request.method === "POST") return handleEtsyWebhook(request, env);
    const callback = /^\/oauth\/(ebay|etsy)\/callback$/.exec(pathname);
    if (callback && request.method === "GET") return handleOAuthCallback(env, callback[1], url);
    if (pathname === "/devices" && request.method === "POST") return handleRegister(request, env);
    if (pathname.startsWith("/studio/")) {
      const deviceId = await authenticate(request, env);
      if (!deviceId) return json({ error: "unauthorized" }, 401);
      return handleStudio(request, env, url, deviceId);
    }
    return json({ error: "not found" }, 404);
  },
};
