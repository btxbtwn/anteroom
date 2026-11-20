import { expect, test } from "./fixtures";

const HOSTED = "https://account.example.test";

test("My Account opens the configured account without entering the listing copy flow", async ({ page, phone }) => {
  test.skip(phone, "The Mac app's window is never phone-sized");
  await page.addInitScript(() => { (window as Window & { pywebview?: object }).pywebview = {}; });
  await page.route("**/api/account-move/destination", route => route.fulfill({
    json: { hosted_url: `${HOSTED}/?sign-in` },
  }));
  const copyRequests: string[] = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname === "/api/account-move") copyRequests.push(request.method());
  });
  // Stand in for the hosted app: the test never leaves this machine.
  await page.route(`${HOSTED}/**`, route => route.fulfill({ contentType: "text/html", body: "<h1>Your account</h1>" }));
  await page.goto("/");
  await page.getByRole("button", { name: "My Account", exact: true }).click();
  const account = page.getByRole("dialog", { name: "Anteroom account", exact: true });
  await expect(account).toContainText("Provider sign-in on this Mac is separate");
  await expect(account.getByLabel("Password")).toHaveCount(0);
  await account.getByRole("button", { name: "Open My Account", exact: true }).click();
  await expect(page).toHaveURL(`${HOSTED}/?sign-in`);
  await expect(page.getByRole("heading", { name: "Your account" })).toBeVisible();
  expect(copyRequests).toEqual([]);
});

test("copying local listings is a separate explicit choice, never an account status check", async ({ page, phone }) => {
  test.skip(phone, "The Mac app's window is never phone-sized");
  await page.addInitScript(() => { (window as Window & { pywebview?: object }).pywebview = {}; });
  await page.route("**/api/account-move/destination", route => route.fulfill({ json: { hosted_url: `${HOSTED}/?sign-in` } }));
  const copyRequests: string[] = [];
  await page.route("**/api/account-move", route => {
    copyRequests.push(route.request().method());
    return route.fulfill({ json: { listings: 3, photos: 6, moved: null, progress: { state: "idle" }, hosted_url: HOSTED } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "My Account", exact: true }).click();
  const account = page.getByRole("dialog", { name: "Anteroom account", exact: true });
  await expect(account).toBeVisible();
  expect(copyRequests).toEqual([]);
  await account.getByRole("button", { name: "Copy this Mac’s listings…" }).click();
  const dialog = page.getByRole("dialog", { name: "Move this Mac to your account" });
  await expect(dialog).toContainText("Sign in to copy 3 listings and 6 photos");
  await expect(dialog.getByLabel("Password")).toBeVisible();
  expect(copyRequests).toEqual(["GET"]);
});

test("in a browser, this Mac's listings show no native account button", async ({ page, phone }) => {
  test.skip(phone, "Account controls sit in the desktop top bar");
  await page.goto("/");
  await expect(page.getByRole("complementary", { name: "Listings" })).toBeVisible();
  await expect(page.getByRole("button", { name: "My Account", exact: true })).toHaveCount(0);
});
