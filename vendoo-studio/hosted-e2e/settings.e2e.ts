import { test, expect } from "@playwright/test";

test("on the web, Settings → Connections shows the marketplace accounts", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "The settings sidebar is the desktop layout");
  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const sections = page.getByRole("navigation", { name: "Settings sections" });
  await sections.getByRole("button", { name: "Connections" }).click();
  // It used to fall back to the AI provider settings.
  await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible();
  await expect(page.getByText("eBay").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Listing AI" })).toHaveCount(0);
  // Chrome's marketplace sign-ins are read through this seller's tab; none is connected in the sandbox.
  for (const name of ["Poshmark", "Mercari", "Depop", "Sellwild", "Vinted", "Facebook"]) {
    await expect(page.locator(`#chrome-${name.toLowerCase()}`)).toContainText("Connect Chrome first");
  }
  await expect(page.getByText("Open Anteroom in Chrome on your computer and connect Chrome first.")).toBeVisible();
  await page.locator("#chrome-poshmark").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "e2e/results/hosted/connections-desktop.png" });
});
