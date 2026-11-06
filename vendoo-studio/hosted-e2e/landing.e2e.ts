import { test, expect } from "@playwright/test";

test("a signed-out visitor sees the landing page and can create an account or sign in", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("One listing. Every storefront.");
  const markets = page.getByRole("list", { name: "Marketplaces filled from this listing" });
  for (const name of ["eBay", "Poshmark", "Mercari", "Depop", "Etsy"]) await expect(markets).toContainText(name);

  await page.getByRole("link", { name: "Create your account" }).first().click();
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();

  await page.goto("/");
  await page.getByRole("link", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
});

test("a link to a listing goes to sign-in, not the landing page", async ({ page }) => {
  await page.goto("/?listing=missing");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
