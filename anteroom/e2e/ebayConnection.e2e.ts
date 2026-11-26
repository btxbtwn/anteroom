import { expect, test } from "./fixtures";

const message = "Reconnect eBay in Settings → Connections. Its sign-in is no longer valid.";

test("a refused eBay sign-in is visible in Connections without starting sign-in", async ({ page, phone }, info) => {
  test.setTimeout(90_000);
  const mutations: string[] = [];
  await page.route(/\/api\/updates(?:\/progress)?$/, route => route.fulfill({ json: { available: false, status: "idle" } }));
  await page.route("**/api/marketplace-accounts**", async route => {
    if (route.request().method() !== "GET") mutations.push(route.request().url());
    await route.fulfill({ json: { accounts: { ebay: {
      connected: false, label: "Sample seller", connectedAt: 1, pending: false, authorizeUrl: null, error: message,
    } }, relay: { running: false, connected: false, lastError: null, skippedEvent: null, lastEventAt: null } } });
  });
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Connections", exact: true }).click();
  const row = page.locator("#direct-ebay");
  await expect(row).toContainText("Not connected", { timeout: 45_000 });
  await expect(row).toContainText(message);
  await expect(row.getByRole("button", { name: "Connect", exact: true })).toBeVisible();
  await expect(row).not.toContainText("Connected as");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), scheme === "dark");
    await row.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`ebay-connection-${scheme}.png`) });
  }
  expect(mutations).toEqual([]);
});

test("disconnected eBay leads with connection and keeps local sale plans available", async ({ page, phone }, info) => {
  await page.route("**/api/marketplace-accounts**", route => route.fulfill({
    json: { accounts: { ebay: { connected: false, pending: false } } },
  }));
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Marketplaces", exact: true }).click();
  await expect(page.getByRole("button", { name: "Connect eBay", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Plan a sale", exact: true })).toBeHidden();
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), scheme === "dark");
    await page.screenshot({ path: info.outputPath(`ebay-workspace-${scheme}.png`) });
  }
  await page.getByText("Sale calendar", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Plan a sale", exact: true })).toBeEnabled();
});
