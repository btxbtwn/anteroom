import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/**
 * Sidebar pages open with their data already in: the app reads each page's
 * first data and loads its code once it has settled, so a tap shows the page
 * at once, with no page spinner or placeholder first. A small "updating"
 * spinner beside data already shown is fine.
 */

async function watchForLoading(page: Page) {
  await page.evaluate(() => {
    const seen: string[] = [];
    (window as unknown as { loadingSeen: string[] }).loadingSeen = seen;
    const check = () => {
      for (const element of document.querySelectorAll('.main-panel [role="status"][aria-busy="true"], .main-panel [role="status"][aria-label="Loading"]')) {
        seen.push(element.getAttribute("aria-label") || "loading");
      }
    };
    new MutationObserver(check).observe(document.body, { subtree: true, childList: true, attributes: true });
  });
}

const loadingSeen = (page: Page) => page.evaluate(() => (window as unknown as { loadingSeen: string[] }).loadingSeen);

test("sidebar pages open with their data already loaded", async ({ page, phone }, info) => {
  const warmed = Promise.all(["/api/sourcing", "/api/automations", "/api/marketplace-accounts"].map((path) =>
    page.waitForResponse((response) => new URL(response.url()).pathname === path)));
  // The dev server loads hundreds of modules; keep timings for all of them.
  await page.addInitScript(() => performance.setResourceTimingBufferSize(10_000));
  await page.goto("/");
  await warmed;
  // Page code loads one chunk per idle turn after the reads.
  const loaded = () => page.evaluate(() => ["SourcingPage", "AutomationsPage", "MarketplacesPage", "SettingsPage"]
    .filter((name) => performance.getEntriesByType("resource").some((entry) => entry.name.includes(`/${name}`))));
  await expect.poll(loaded, { timeout: 15_000 }).toHaveLength(4);
  await watchForLoading(page);

  const pages = [
    { name: "Sourcing", ready: page.getByText("Source with sales evidence", { exact: true }) },
    { name: "Automations", ready: page.getByRole("heading", { name: "Automations", exact: true }) },
    { name: "Marketplaces", ready: page.getByRole("tab", { name: "eBay" }) },
    { name: "Settings", ready: page.getByRole("heading", { name: phone ? "Settings" : "Theme", exact: true }) },
  ];
  for (const { name, ready } of pages) {
    if (phone) await page.getByRole("button", { name: "Open navigation" }).first().click();
    await page.evaluate(() => { (window as unknown as { loadingSeen: string[] }).loadingSeen.length = 0; });
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name, exact: true }).click();
    if (name === "Sourcing") await page.getByRole("tab", { name: "Wholesale boxes" }).click();
    await expect(ready.first()).toBeVisible();
    await page.screenshot({ path: info.outputPath(`${name.toLowerCase()}.png`) });
    expect(await loadingSeen(page), `${name} showed a loading state`).toEqual([]);
  }
});
