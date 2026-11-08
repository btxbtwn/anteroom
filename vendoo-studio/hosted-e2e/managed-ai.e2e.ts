import { test, expect } from "@playwright/test";

test.setTimeout(90_000);

test("included AI needs no personal key and disables personal billing fallback", async ({ page }, info) => {
  // The real hosted sandbox handles sessions; managed readiness is simulated
  // because it intentionally cannot hold or use a company API credential.
  await page.route("**/api/settings/provider", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: {
      ...await response.json(), provider: "anteroom", primary: "anteroom", fallback: "none",
      configured: true, managed_ai_available: true, listing_model: "gpt-6-luna", vision_model: "gpt-6-luna",
    } });
  });
  await page.goto("/?sign-in", { waitUntil: "domcontentloaded" });
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  if (info.project.name === "phone") {
    // Other hosted tests can leave this seller's saved home on Assistant.
    // Its navigation is already reachable; the listing editor uses Inventory.
    const navigation = page.getByRole("button", { name: "Open navigation", exact: true });
    if (!await navigation.isVisible()) await page.getByRole("button", { name: "Inventory", exact: true }).click();
    await navigation.click();
  }
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Providers", exact: true }).click();
  const close = page.getByRole("button", { name: "Close navigation" });
  if (await close.isVisible()) await close.click();
  await expect(page.getByText(/No personal API key is needed/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("combobox", { name: "Fallback listing AI" })).toBeDisabled();
  await page.screenshot({ path: `e2e/results/hosted/managed-ai-${info.project.name}.png`, fullPage: true });
});
