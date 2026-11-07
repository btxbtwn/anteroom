import { test, expect } from "@playwright/test";

test("account menu holds extension connection and sign out", async ({ page }, info) => {
  await page.goto("/?sign-in");
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

test("account menu deletes the account only after a confirmation that says live listings stay", async ({ page }, info) => {
  // Bob, so Alice's seeded workspace stays for the other tests. The sandbox keeps
  // its sign-in accounts, so Bob can sign in again to an empty workspace.
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("bob@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const account = page.getByRole("button", { name: "Account", exact: true });
  await account.click();
  await page.getByRole("button", { name: "Delete account…", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText("Delete your Anteroom account?")).toBeVisible();
  await expect(dialog.getByText(/does not delist them/)).toBeVisible();
  await page.screenshot({ path: `e2e/results/hosted/delete-account-${info.project.name}.png` });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(account).toBeVisible();

  await account.click();
  await page.getByRole("button", { name: "Delete account…", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete account", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
});
