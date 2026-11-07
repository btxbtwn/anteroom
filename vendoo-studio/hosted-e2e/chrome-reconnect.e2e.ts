import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

// The simulated Bridge is current; an older one makes the site ask for an update.
const BRIDGE_VERSION: string = JSON.parse(readFileSync(new URL("../../vendoo-extension/manifest.json", import.meta.url), "utf8")).version;

test("an approved Chrome recovers from a missing Bridge without another Connect click", async ({ page, baseURL }, info) => {
  test.setTimeout(60000);
  let disconnected = false;
  let signingOut = false;
  const signOutEvents: string[] = [];
  const relayRequests: string[] = [];
  let heartbeats = 0;
  await page.exposeFunction("onBridgeDisconnect", async () => {
    // An actual extension reply is asynchronous and can arrive after a navigation was started.
    await new Promise(resolve => setTimeout(resolve, 500));
    disconnected = true;
    signOutEvents.push("revoked");
  });
  page.on("request", request => {
    if (request.url().includes("/api/browser-relay/next?")) relayRequests.push(request.url());
    if (request.url().endsWith("/api/browser-relay/heartbeat")) heartbeats++;
    if (signingOut && request.isNavigationRequest() && request.frame() === page.mainFrame()) signOutEvents.push("navigation");
  });
  // Use the isolated hosted sandbox as the trusted origin; no live site or extension is contacted.
  await page.route(/\/src\/lib\/macApp\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: (await response.text()).replace("https://myanteroom.com", new URL(baseURL!).origin) });
  });
  await page.route("**/api/browser-relay/next?**", route => route.abort());
  // The simulated Bridge must not leave presence behind for other tests.
  await page.route("**/api/browser-relay/heartbeat", route => route.abort());
  await page.addInitScript((bridgeVersion: string) => {
    const state = window as Window & { bridgeReady: boolean; connectClicks: number; onBridgeDisconnect: () => Promise<void> };
    state.bridgeReady = false;
    state.connectClicks = 0;
    window.addEventListener("message", event => {
      const data = event.data;
      if (event.source !== window || data?.source !== "anteroom-cloud") return;
      if (data.action === "connect") state.connectClicks++;
      if (!state.bridgeReady) return;
      if (data.action === "disconnect") {
        void state.onBridgeDisconnect().then(() => window.postMessage({ source: "anteroom-extension", version: 1,
          requestId: data.requestId, ok: true, state: "disconnected", extensionVersion: bridgeVersion }, location.origin));
        return;
      }
      window.postMessage({ source: "anteroom-extension", version: 1, requestId: data.requestId,
        ok: true, state: "connected", extensionVersion: bridgeVersion,
        supportedActions: ["marketplace.actions", "marketplace.sessions", "marketplace.sales_history"] }, location.origin);
    });
  }, BRIDGE_VERSION);
  // Bob, not Alice: the server keeps a Bridge present for 90 seconds, and the tests that
  // follow expect Alice's Chrome never to have connected.
  await page.clock.install();
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("bob@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await page.clock.fastForward(2500);
  await expect(page.getByRole("button", { name: "Connect Anteroom Extension", exact: true })).toBeVisible();
  await page.evaluate(() => { (window as Window & { bridgeReady: boolean }).bridgeReady = true; });
  await page.clock.fastForward(15000);
  await expect(page.getByRole("button", { name: "Chrome connected", exact: true })).toBeVisible();
  await expect.poll(() => relayRequests.some(url => url.includes("marketplace.sales_history"))).toBe(true);
  await expect.poll(() => heartbeats).toBeGreaterThan(0);
  expect(await page.evaluate(() => (window as Window & { connectClicks: number }).connectClicks)).toBe(0);
  await page.getByRole("button", { name: "Chrome connected", exact: true }).click();
  await page.screenshot({ path: `e2e/results/hosted/chrome-reconnect-${info.project.name}.png` });
  await page.keyboard.press("Escape");
  if (!await page.getByRole("button", { name: "Sign out", exact: true }).isVisible()) {
    await page.getByRole("button", { name: "Account", exact: true }).click();
  }
  signingOut = true;
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible({ timeout: 15000 });
  expect(disconnected).toBe(true);
  expect(signOutEvents.slice(0, 2)).toEqual(["revoked", "navigation"]);
});
