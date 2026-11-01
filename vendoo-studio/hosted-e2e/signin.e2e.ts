import { test, expect, type Page } from "@playwright/test";

async function signIn(page: Page, email: string, password: string) {
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

test("a seller signs in with an email and password", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "alice@sandbox.test", "wrong password");
  await expect(page.getByRole("alert")).toContainText("don’t match");
  await signIn(page, "alice@sandbox.test", "sandbox");
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
});

test("a new seller creates an account and is asked to confirm their email", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Create an account" }).click();
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("a long password");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("alert")).toContainText("Sign in instead");
  await page.getByLabel("Email address").fill(`new-${Date.now()}@example.com`);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("status")).toContainText("sent a confirmation link");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("a seller who forgot their password sets a new one from the reset link", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Forgot password?" }).click();
  await page.getByLabel("Email address").fill("bob@sandbox.test");
  await page.getByRole("button", { name: "Email me a reset link" }).click();
  await expect(page.getByRole("status")).toContainText("link to set a new password");
  // The sandbox sends no email; open the link it would contain, in this browser.
  await page.goto("/?code=recovery-bob");
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await page.getByLabel("New password").fill("a new password");
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
});

test("an email link opened in another browser asks the seller to sign in", async ({ page }) => {
  await page.goto("/?code=not-from-this-browser");
  await expect(page.getByRole("status").first()).toContainText("different browser or app");
  await expect(page).toHaveURL(/\/$/);
});

test("in the Mac app, password resets open in the browser", async ({ page }) => {
  await page.addInitScript(() => { (window as Window & { pywebview?: object }).pywebview = {}; });
  await page.goto("/");
  const reset = page.getByRole("link", { name: "Forgot password? Reset it in your browser" });
  await expect(reset).toHaveAttribute("target", "_blank");
  await expect(reset).toHaveAttribute("href", /\/\?forgot-password$/);
  await expect(page.getByText("Account › This Mac’s Listings")).toBeVisible();
  await signIn(page, "alice@sandbox.test", "sandbox");
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect Chrome" })).toHaveCount(0);
});

test("the browser opens straight to the reset form from the Mac app", async ({ page }) => {
  await page.goto("/?forgot-password");
  await expect(page.getByRole("heading", { name: "Reset your password" })).toBeVisible();
});
