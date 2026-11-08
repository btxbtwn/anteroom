import { test, expect } from "@playwright/test";

test("on a phone the account button ends the page header row instead of a band above it", async ({ page, isMobile }, info) => {
  test.skip(!isMobile, "Phone layout only; the desktop top bar has its own account button");
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  // Signed in, the assistant is home; the inventory sits behind the navigation menu.
  await page.getByRole("button", { name: "Open navigation" }).filter({ visible: true }).click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  const account = page.getByRole("button", { name: "Account", exact: true });
  const menu = page.getByRole("button", { name: "Open navigation" }).first();
  const select = page.getByRole("button", { name: "Select listings" });
  await expect(account).toBeVisible();
  await expect(select).toBeVisible();
  const [accountBox, menuBox, selectBox] = await Promise.all([account.boundingBox(), menu.boundingBox(), select.boundingBox()]);
  // Same row as the header's own buttons, and clear of them.
  expect(Math.abs((accountBox!.y + accountBox!.height / 2) - (menuBox!.y + menuBox!.height / 2))).toBeLessThanOrEqual(2);
  expect(accountBox!.x).toBeGreaterThanOrEqual(selectBox!.x + selectBox!.width);
  expect(accountBox!.height).toBeGreaterThanOrEqual(44);
  await page.screenshot({ path: `e2e/results/hosted/phone-header-${info.project.name}.png` });
});
