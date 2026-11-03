import { test, expect } from "@playwright/test";

test("account menu holds extension connection and sign out", async ({ page }, info) => {
  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const account = page.getByRole("button", { name: "Account", exact: true });
  await expect(account).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeHidden();
  await account.click();
  await expect(page.getByText("alice@sandbox.test", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect Anteroom Extension", exact: true })).toBeVisible();
  await page.screenshot({ path: `e2e/results/hosted/account-${info.project.name}.png` });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeHidden();
  await account.click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
});
