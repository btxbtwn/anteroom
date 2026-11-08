import { test, expect } from "@playwright/test";

test("on a phone the account button ends the page header row instead of a band above it", async ({ page, request, isMobile }, info) => {
  test.skip(!isMobile, "Phone layout only; the desktop top bar has its own account button");
  test.setTimeout(90_000);
  const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;
  const session = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  })).json();
  for (let index = 0; index < 24; index++) {
    const response = await request.post(`${api}/api/drafts`, {
      headers: { Authorization: `Bearer ${session.access_token}` }, data: { title: `Phone scroll ${index}` },
    });
    expect(response.status()).toBe(201);
  }
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Inventory", exact: true }).click();
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
  // The document owns scrolling: an inner clipped scrollport prevents Safari
  // from painting the inventory under its floating address bar.
  await expect(page.locator(".app-shell")).toHaveClass(/phone-inventory/);
  expect(await page.locator(".sidebar").evaluate(el => getComputedStyle(el).overflowY)).toBe("visible");
  expect(await page.locator("html").evaluate(el => getComputedStyle(el).overflowY)).toBe("visible");
  await account.click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).not.toBeVisible();
  await expect.poll(() => page.evaluate(() => {
    window.scrollTo(0, document.documentElement.scrollHeight);
    return window.scrollY;
  })).toBeGreaterThan(500);
  const list = page.getByRole("list", { name: "Listings", exact: true });
  await expect.poll(async () => {
    const last = list.getByRole("listitem").last();
    return last.evaluate(el => el.getAttribute("aria-posinset") === el.getAttribute("aria-setsize"));
  }).toBe(true);
  await expect(account).toBeInViewport();
  const scrollY = await page.evaluate(() => window.scrollY);
  await list.getByRole("listitem").last().getByRole("link").click();
  await page.getByRole("button", { name: "Inventory", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(scrollY - 50);
  await page.screenshot({ path: `e2e/results/hosted/phone-header-${info.project.name}.png` });
});

test("desktop inventory keeps panel scrolling", async ({ page, isMobile }, info) => {
  test.skip(isMobile, "Desktop layout");
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "Listings", exact: true })).toBeVisible();
  await expect(page.locator(".app-shell")).not.toHaveClass(/phone-inventory/);
  expect(await page.locator("html").evaluate(el => getComputedStyle(el).overflowY)).toBe("hidden");
  await page.screenshot({ path: `e2e/results/hosted/inventory-${info.project.name}.png` });
});
