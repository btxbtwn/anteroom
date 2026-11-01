import { expect, test } from "./fixtures";

const HOSTED = "https://peaceful-meadowstone-6944.fly.dev";

test("in the Mac app, Sign in opens the seller's account", async ({ page, phone }) => {
  test.skip(phone, "The Mac app's window is never phone-sized");
  await page.addInitScript(() => { (window as Window & { pywebview?: object }).pywebview = {}; });
  // Stand in for the hosted app: the test never leaves this machine.
  await page.route(`${HOSTED}/**`, route => route.fulfill({ contentType: "text/html", body: "<h1>Your account</h1>" }));
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(`${HOSTED}/`);
  await expect(page.getByRole("heading", { name: "Your account" })).toBeVisible();
});

test("in a browser, this Mac's listings show no Sign in button", async ({ page, phone }) => {
  test.skip(phone, "Account controls sit in the desktop top bar");
  await page.goto("/");
  await expect(page.getByRole("complementary", { name: "Listings" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toHaveCount(0);
});
