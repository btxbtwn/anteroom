import type { Page } from "@playwright/test";
import { createListing, expect, openListing, showStatusTab, test, titleBox } from "./fixtures";

/** On a phone the inventory sits behind the navigation menu; home is the assistant. */
async function showInventory(page: Page, phone: boolean) {
  if (!phone) return;
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
}

/** Click New listing and return the draft it made. */
async function newListing(page: Page): Promise<string> {
  const created = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/conversations" && response.request().method() === "POST");
  await page.getByRole("button", { name: "New listing", exact: true }).first().click();
  return (await (await created).json()).id;
}

test("the inventory shows placeholder rows until it loads, and opens on Active", async ({ page, phone, request }, info) => {
  const title = `Loading ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/conversations", async (route) => {
      if (route.request().method() === "GET") await held;
      await route.continue();
    });
    await page.goto("/");
    await showInventory(page, phone);
    const loading = page.getByRole("status", { name: "Loading listings" });
    await expect(loading).toBeVisible();
    await expect(page.getByText("No listings yet")).toHaveCount(0);
    const tabs = page.getByRole("tablist", { name: "Listing status" });
    // No count until there is one to show.
    await expect(tabs.getByRole("tab", { name: "Active", exact: true })).toBeVisible();
    await page.screenshot({ path: `e2e/results/inventory-loading-${info.project.name}.png` });
    release();
    await expect(loading).toHaveCount(0);
    await expect(tabs.getByRole("tab", { name: /^Active \d+$/ })).toHaveAttribute("aria-selected", "true");
    await expect(tabs.getByRole("tab")).toHaveText([/^Draft/, /^Active/, /^Sold/]);
    // Anything not live or sold, like this new draft, is under Draft.
    await expect(page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true })).toHaveCount(0);
    await showStatusTab(page, "Draft");
    await expect(page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true })).toBeVisible();
    await page.screenshot({ path: `e2e/results/inventory-loaded-${info.project.name}.png` });
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("a new listing left without a photo or a word is not kept", async ({ page, phone, request }, info) => {
  const title = `Other ${info.project.name} ${Date.now()}`;
  const other = await createListing(request, title);
  try {
    await page.goto("/");
    await showInventory(page, phone);
    const fresh = await newListing(page);
    expect((await request.get(`/api/conversations/${fresh}`)).status()).toBe(200);
    if (phone) await page.getByRole("button", { name: "Inventory", exact: true }).click();
    // A new listing opens its tab, Draft, where the other draft is too.
    await expect(page.getByRole("tablist", { name: "Listing status" }).getByRole("tab", { name: /^Draft\b/ }))
      .toHaveAttribute("aria-selected", "true");
    await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true }).click();
    await expect.poll(async () => (await request.get(`/api/conversations/${fresh}`)).status(), { timeout: 15_000 }).toBe(404);
    await expect(page.getByRole("list", { name: "Listings" }).getByRole("link", { name: "New Listing", exact: true })).toHaveCount(0);
  } finally {
    await request.delete(`/api/conversations/${other}`);
  }
});

test("a new listing the seller typed into is kept", async ({ page, phone, request }, info) => {
  const title = `Other ${info.project.name} ${Date.now()}`;
  const other = await createListing(request, title);
  let fresh = "";
  try {
    await page.goto("/");
    await showInventory(page, phone);
    fresh = await newListing(page);
    const field = await titleBox(page, phone);
    await field.fill(`Typed ${info.project.name}`);
    if (phone) await page.getByRole("button", { name: "Inventory", exact: true }).click();
    // A new listing opens its tab, Draft, where the other draft is too.
    await expect(page.getByRole("tablist", { name: "Listing status" }).getByRole("tab", { name: /^Draft\b/ }))
      .toHaveAttribute("aria-selected", "true");
    await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true }).click();
    await expect.poll(async () => {
      const saved = await (await request.get(`/api/conversations/${fresh}/listing`)).json();
      return String(saved.listing?.title ?? "");
    }, { timeout: 15_000 }).toBe(`Typed ${info.project.name}`);
    // Past the point where an untouched draft would have gone.
    await page.waitForTimeout(1_000);
    expect((await request.get(`/api/conversations/${fresh}`)).status()).toBe(200);
  } finally {
    if (fresh) await request.delete(`/api/conversations/${fresh}`);
    await request.delete(`/api/conversations/${other}`);
  }
});


test("openListing finds a draft after a slow initial inventory load", async ({ page, phone, request }, info) => {
  const title = `Slow draft ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await page.route("**/api/conversations", async route => {
      // Deliberately exceed the helper's former three-second Draft window.
      if (route.request().method() === "GET") await new Promise(resolve => setTimeout(resolve, 4_000));
      await route.continue();
    });
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toHaveValue(title);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
