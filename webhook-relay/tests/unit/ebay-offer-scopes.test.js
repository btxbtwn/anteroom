import assert from "node:assert/strict";
import test from "node:test";
import { EBAY_USER_SCOPES, ebayAuthorizeUrl, ebayRefresh } from "../../src/ebay.js";

test("all workspace permissions use scopes assigned to the production keyset", () => {
  const features = ["analytics", "finances", "orders", "disputes", "messages", "feedback", "marketing", "listings"];
  const url = new URL(ebayAuthorizeUrl({ EBAY_CLIENT_ID: "app", EBAY_RUNAME: "test-runame" }, "state", features));
  const scopes = url.searchParams.get("scope").split(" ");
  const granted = ["", "/sell.fulfillment.readonly", "/sell.inventory", "/commerce.identity.readonly",
    "/commerce.notification.subscription", "/sell.analytics.readonly", "/sell.finances", "/sell.fulfillment",
    "/sell.payment.dispute", "/commerce.message", "/commerce.feedback", "/sell.marketing", "/sell.account.readonly"];
  assert.deepEqual(scopes.sort(), granted.map((scope) => `https://api.ebay.com/oauth/api_scope${scope}`).sort());
});

test("eBay sign-in requests offer permission; refresh retains the original grant", async () => {
  assert(EBAY_USER_SCOPES.includes("https://api.ebay.com/oauth/api_scope/sell.inventory"));
  const env = { EBAY_CLIENT_ID: "app", EBAY_CLIENT_SECRET: "test-secret", EBAY_RUNAME: "test-runame" };
  const url = new URL(ebayAuthorizeUrl(env, "state"));
  assert(url.searchParams.get("scope").includes("sell.inventory"));
  const originalFetch = globalThis.fetch;
  let request;
  globalThis.fetch = async (_url, init) => {
    request = new URLSearchParams(init.body);
    return new Response(JSON.stringify({ access_token: "test-token" }));
  };
  try {
    await ebayRefresh(env, "old-grant");
    assert.equal(request.get("refresh_token"), "old-grant");
    assert.equal(request.has("scope"), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
