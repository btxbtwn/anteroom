import assert from "node:assert/strict";
import test from "node:test";
import { EBAY_USER_SCOPES, ebayAuthorizeUrl, ebayRefresh } from "../../src/ebay.js";

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
