import { expect, test } from "./fixtures";

const HOSTED = "https://peaceful-meadowstone-6944.fly.dev";

test("in the Mac app, Sign in offers to move this Mac's listings, or skip to the account", async ({ page, phone }) => {
  test.skip(phone, "The Mac app's window is never phone-sized");
  await page.addInitScript(() => { (window as Window & { pywebview?: object }).pywebview = {}; });
  // Stand in for the hosted app: the test never leaves this machine.
  await page.route(`${HOSTED}/**`, route => route.fulfill({ contentType: "text/html", body: "<h1>Your account</h1>" }));
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Move this Mac to your account" });
  await expect(dialog).toContainText(/Sign in to copy \d+ listings? and \d+ photos?/);
  // The sandbox refuses outbound connections, so the move explains it could not reach the account.
  await dialog.getByLabel("Email address").fill("seller@example.com");
  await dialog.getByLabel("Password").fill("a password");
  await dialog.getByRole("button", { name: "Move to my account" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Could not reach your account");
  await expect(dialog.getByRole("button", { name: "Try again" })).toBeVisible();
  await dialog.getByRole("button", { name: "Skip for now" }).click();
  await expect(page).toHaveURL(`${HOSTED}/`);
  await expect(page.getByRole("heading", { name: "Your account" })).toBeVisible();
});

test("in a browser, this Mac's listings show no Sign in button", async ({ page, phone }) => {
  test.skip(phone, "Account controls sit in the desktop top bar");
  await page.goto("/");
  await expect(page.getByRole("complementary", { name: "Listings" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toHaveCount(0);
});
