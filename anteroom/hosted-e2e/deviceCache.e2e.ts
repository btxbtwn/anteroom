import { test, expect, type Page } from "@playwright/test";
import { showStatusTab } from "../e2e/fixtures";

/**
 * A reload opens on the workspace kept in this browser, before the server
 * answers; signing out erases it, and another seller never sees it.
 */

const api = `http://127.0.0.1:${process.env.ANTEROOM_PORT || "4341"}`;

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
  await showStatusTab(page, "Draft");
  const row = page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true });
  await expect(row).toBeVisible();
  await expect.poll(() => keptKeys(page)).toEqual(expect.arrayContaining(["owner", "queries", "runtime"]));

  // The server does not answer at all: whatever shows came from this browser.
  let held = 0;
  await page.route((url) => url.pathname.startsWith("/api/"), () => { held += 1; });
  await page.reload({ waitUntil: "commit" });
  await openInventory(page, isMobile);
  await showStatusTab(page, "Draft");
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
  await showStatusTab(page, "Draft");
  await expect(page.getByRole("tablist", { name: "Listing status" })).toBeVisible();
  await expect(page.getByText(title)).toHaveCount(0);
});

/** Thumbnail cache names kept in Cache Storage for sellers on this browser. */
function thumbnailCaches(page: Page) {
  return page.evaluate(async () => (await caches.keys()).filter((name) => name.startsWith("anteroom-thumbnails:")));
}

test("erases a previous seller's caches when their session ends while closed and prunes others on sign-in", async ({ page, request, isMobile }, info) => {
  const login = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "alice@sandbox.test", password: "sandbox" } })).json();
  const title = `Expired session ${info.project.name} ${Date.now()}`;
  const created = await request.post(`${api}/api/drafts`, { headers: { Authorization: `Bearer ${login.access_token}` }, data: { title } });
  expect(created.status()).toBe(201);

  await signIn(page, "alice@sandbox.test");
  await openInventory(page, isMobile);
  await showStatusTab(page, "Draft");
  await expect(page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true })).toBeVisible();
  await expect.poll(() => keptKeys(page)).toEqual(expect.arrayContaining(["owner", "queries", "runtime"]));
  // Seed Alice's thumbnail cache as if she viewed listing photos on this browser.
  await page.evaluate(async (sellerId: string) => {
    const cache = await caches.open(`anteroom-thumbnails:${sellerId}`);
    await cache.put("/api/photos/p1/thumb?size=96&v=1", new Response("jpeg"));
  }, login.user.id);
  expect(await thumbnailCaches(page)).toEqual([`anteroom-thumbnails:${login.user.id}`]);

  // Simulate Alice's session expiring while the tab was closed: clear auth storage and reload fresh.
  await page.evaluate(() => localStorage.clear());
  await page.goto("/?sign-in");
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await expect.poll(() => keptKeys(page)).toEqual([]);
  await expect.poll(() => thumbnailCaches(page)).toEqual([]);

  // Seed another seller's leftover thumbnail cache, then sign in as Bob on the same page.
  await page.evaluate(async () => {
    const cache = await caches.open("anteroom-thumbnails:stale-seller");
    await cache.put("/api/photos/stale/thumb?size=96&v=1", new Response("jpeg"));
  });
  await page.getByLabel("Email address").fill("bob@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await openInventory(page, isMobile);
  await showStatusTab(page, "Draft");
  await expect(page.getByRole("tablist", { name: "Listing status" })).toBeVisible();
  // Bob's workspace persists (the signed-out clear did not lock out storage), and the stale thumbnail cache is pruned.
  await expect.poll(() => keptKeys(page)).toEqual(expect.arrayContaining(["owner", "queries", "runtime"]));
  await expect.poll(() => thumbnailCaches(page)).not.toContain("anteroom-thumbnails:stale-seller");
});
