import { test, expect } from "@playwright/test";

test("hosted managed AI hides Providers and the composer model picker", async ({ page }, info) => {
  const phone = info.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });

  await page.route("**/api/runtime", async (route) => {
    const response = await route.fetch();
    const json = await response.json();
    const features = Array.isArray(json.features) ? json.features : [];
    if (!features.includes("managed_ai")) features.push("managed_ai");
    await route.fulfill({ response, json: { ...json, features } });
  });

  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();

  // Open Assistant and verify the composer omits model/provider controls.
  if (phone) await page.getByRole("button", { name: "Open navigation", exact: true }).click();
  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  if (phone) {
    const close = page.getByRole("button", { name: "Close navigation", exact: true });
    if (await close.isVisible()) await close.click();
  }
  const leftControls = page.locator('[data-chat-composer-controls="left"]');
  await expect(leftControls).toBeVisible();
  await expect(leftControls.locator("button")).toHaveCount(0);
  await page.screenshot({ path: `e2e/results/hosted/managed-ai-assistant-${info.project.name}.png` });

  // Open Settings and verify Providers is omitted from sections and search.
  if (phone) await page.getByRole("button", { name: "Open navigation", exact: true }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const sections = page.getByRole("navigation", { name: "Settings sections" });
  await expect(sections.getByRole("button", { name: "General", exact: true })).toBeVisible();
  await expect(sections.getByRole("button", { name: "Providers", exact: true })).toHaveCount(0);
  await page.screenshot({ path: `e2e/results/hosted/managed-ai-settings-${info.project.name}.png` });

  await page.getByRole("combobox", { name: "Search settings" }).fill("listing ai");
  await expect(page.getByText("No settings found")).toBeVisible();
});
