import type { Locator, Page } from "@playwright/test";
import { createListing, expect, test } from "./fixtures";

/** Layout checks from the UI audit: text never runs under controls or gets cut short with room to spare. */

const LONG_TITLE = "Madewell 28 Black High Rise Skinny Jeans Stretch Denim";

async function openPage(page: Page, name: string, phone = false) {
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).first().click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name, exact: true }).click();
}

/** Width of a string in the element's own font. */
async function textWidth(locator: Locator, text: string): Promise<number> {
  return locator.evaluate((element, value) => {
    const context = document.createElement("canvas").getContext("2d")!;
    context.font = getComputedStyle(element).font;
    return context.measureText(value).width;
  }, text);
}

test.describe("layout", () => {
  test.skip(({ phone }) => phone, "Desktop layout: hover actions, the side-by-side table and the chats panel");

  test("a hovered sidebar row ends its title before the row's actions", async ({ page, request }) => {
    await createListing(request, LONG_TITLE);
    await page.goto("/");
    const link = page.getByRole("list", { name: "Listings" }).getByRole("link", { name: LONG_TITLE, exact: true });
    await link.hover();
    const rename = page.getByRole("button", { name: `Rename ${LONG_TITLE}` });
    await expect(rename).toBeVisible();
    // Let the padding settle before measuring.
    await expect.poll(() => link.evaluate((element) => getComputedStyle(element).paddingRight)).not.toBe("0px");
    const textRight = await link.evaluate((element) => element.getBoundingClientRect().right - parseFloat(getComputedStyle(element).paddingRight));
    const actionsLeft = (await rename.boundingBox())!.x;
    expect(textRight).toBeLessThanOrEqual(actionsLeft);
    await page.screenshot({ path: "e2e/results/layout/sidebar-row-hover-desktop.png", clip: { x: 0, y: 0, width: 360, height: 900 } });
  });

  test("the inventory table gives titles the free width and leaves search to the sidebar", async ({ page, request }) => {
    const titleText = `${LONG_TITLE} ${crypto.randomUUID().slice(0, 6)}`;
    await createListing(request, titleText);
    await page.goto("/");
    await page.getByRole("button", { name: "Inventory table" }).click();
    const table = page.getByRole("table", { name: "Inventory" });
    const title = table.getByRole("rowheader").filter({ hasText: titleText }).getByText(titleText, { exact: true });
    await expect(title).toBeVisible();
    // Two lines hold the whole title at 1440px; nothing is clipped.
    expect(await title.evaluate((element) => element.scrollHeight <= element.clientHeight + 1)).toBe(true);
    // The sidebar's search box filters the table, so the page shows only that one.
    await expect(page.getByRole("searchbox", { name: /Search listings/ })).toHaveCount(1);
    await page.getByRole("searchbox", { name: /Search listings/ }).fill(titleText);
    await expect(table.getByRole("rowheader")).toHaveCount(1);
    await page.getByRole("searchbox", { name: /Search listings/ }).fill("");
    await page.screenshot({ path: "e2e/results/layout/inventory-table-desktop.png" });
  });

  test("the chats panel shows its whole search placeholder", async ({ page }) => {
    await openPage(page, "Assistant");
    const search = page.getByRole("searchbox", { name: "Search chats" });
    await expect(search).toBeVisible();
    const placeholder = (await search.getAttribute("placeholder")) ?? "";
    const room = await search.evaluate((element) => element.clientWidth);
    expect(await textWidth(search, placeholder)).toBeLessThanOrEqual(room);
    await page.screenshot({ path: "e2e/results/layout/assistant-desktop.png" });
  });

  test("the eBay tab names eBay once and offers one Connect eBay", async ({ page }) => {
    await openPage(page, "Marketplaces");
    const panel = page.getByRole("tabpanel");
    await expect(panel.getByRole("button", { name: "Connect eBay", exact: true })).toHaveCount(1);
    // The tab names the marketplace; the panel's own heading is for screen readers only.
    await expect(panel.getByRole("heading", { name: "eBay", exact: true, level: 2 })).toBeAttached();
    await expect(panel.getByRole("heading", { name: "eBay", exact: true, level: 2 })).toHaveClass(/sr-only/);
    await page.screenshot({ path: "e2e/results/layout/marketplaces-desktop.png" });
  });

  test("every automation row has its switch in the On column", async ({ page }) => {
    await openPage(page, "Automations");
    const rows = page.getByRole("region", { name: "Your automations" }).getByRole("listitem");
    await expect(rows.first()).toBeVisible();
    for (const row of await rows.all()) await expect(row.getByRole("switch")).toHaveCount(1);
    await page.screenshot({ path: "e2e/results/layout/automations-desktop.png" });
  });
});

// Screenshots for review at phone size; CI keeps e2e/results.
test("phone screens after the layout fixes", async ({ page, phone }, info) => {
  test.skip(!phone, "Phone screenshots");
  for (const name of ["Assistant", "Marketplaces", "Automations"]) {
    await openPage(page, name, true);
    await expect(page.getByRole("main")).toBeVisible();
    await page.screenshot({ path: `e2e/results/layout/${name.toLowerCase()}-${info.project.name}.png` });
  }
});
