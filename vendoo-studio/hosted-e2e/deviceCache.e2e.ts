import { test, expect, type Page } from "@playwright/test";

/**
 * A reload opens on the workspace kept in this browser, before the server
 * answers; signing out erases it, and another seller never sees it.
 */

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;

async function signIn(page: Page, email: string) {
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

async function openInventory(page: Page, phone: boolean) {
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).first().click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory", exact: true }).click();
  }
}

/** Keys in the browser's device store for the workspace. */
function keptKeys(page: Page) {
  return page.evaluate(() => new Promise<string[]>((resolve, reject) => {
    const open = indexedDB.open("anteroom-device");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      if (!db.objectStoreNames.contains("workspace")) { db.close(); resolve([]); return; }
      const read = db.transaction("workspace").objectStore("workspace").getAllKeys();
      read.onsuccess = () => { db.close(); resolve(read.result.map(String)); };
      read.onerror = () => reject(read.error);
    };
  }));
}

test("a reload opens on the kept workspace, and signing out erases it", async ({ page, request, isMobile }, info) => {
  const login = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "alice@sandbox.test", password: "sandbox" } })).json();
  const title = `Kept on device ${info.project.name} ${Date.now()}`;
  const created = await request.post(`${api}/api/drafts`, { headers: { Authorization: `Bearer ${login.access_token}` }, data: { title } });
  expect(created.status()).toBe(201);

  await signIn(page, "alice@sandbox.test");
  await openInventory(page, isMobile);
  const row = page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true });
  await expect(row).toBeVisible();
  await expect.poll(() => keptKeys(page)).toEqual(expect.arrayContaining(["owner", "queries", "runtime"]));

  // The server does not answer at all: whatever shows came from this browser.
  let held = 0;
  await page.route((url) => url.pathname.startsWith("/api/"), () => { held += 1; });
  await page.reload({ waitUntil: "commit" });
  await openInventory(page, isMobile);
  await expect(row).toBeVisible();
  expect(held, "the reload asked the server and is still waiting").toBeGreaterThan(0);
  await page.screenshot({ path: `e2e/results/hosted/kept-workspace-${info.project.name}.png` });
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.reload();
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  expect(await keptKeys(page)).toEqual([]);

  await signIn(page, "bob@sandbox.test");
  await openInventory(page, isMobile);
  await expect(page.getByRole("tablist", { name: "Listing status" })).toBeVisible();
  await expect(page.getByText(title)).toHaveCount(0);
});
