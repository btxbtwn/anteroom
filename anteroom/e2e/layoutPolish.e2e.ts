import type { Locator, Page } from "@playwright/test";
import { createListing, expect, showStatusTab, test } from "./fixtures";

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

  test("a hovered sidebar row ends its title before the row's actions", async ({ page, request }, info) => {
    await createListing(request, LONG_TITLE);
    await page.goto("/");
    await showStatusTab(page, "Draft");
    const link = page.getByRole("list", { name: "Listings" }).getByRole("link", { name: LONG_TITLE, exact: true });
    await link.hover();
    const rename = page.getByRole("button", { name: `Rename ${LONG_TITLE}` });
    await expect(rename).toBeVisible();
    // Let the padding settle before measuring.
    await expect.poll(() => link.evaluate((element) => getComputedStyle(element).paddingRight)).not.toBe("0px");
    const textRight = await link.evaluate((element) => element.getBoundingClientRect().right - parseFloat(getComputedStyle(element).paddingRight));
    const actionsLeft = (await rename.boundingBox())!.x;
    expect(textRight).toBeLessThanOrEqual(actionsLeft);
    await page.screenshot({ path: info.outputPath("sidebar-row-hover-desktop.png"), clip: { x: 0, y: 0, width: 360, height: 900 } });
  });

  test("the inventory table gives titles the free width and leaves search to the sidebar", async ({ page, request }, info) => {
    const titleText = `${LONG_TITLE} ${crypto.randomUUID().slice(0, 6)}`;
    await createListing(request, titleText);
    await page.goto("/");
    await page.getByRole("button", { name: "Inventory table" }).click();
    // The table opens on Active; a new listing is a draft.
    await page.getByRole("group", { name: "Listing status" }).getByRole("button", { name: /^Draft\b/ }).click();
    const table = page.getByRole("table", { name: "Inventory" });
    const title = table.getByRole("rowheader").filter({ hasText: titleText }).getByText(titleText, { exact: true });
    await expect(title).toBeVisible();
    // Titles wrap to two lines, with the complete title available on hover.
    await expect(title).toHaveClass(/line-clamp-2/);
    await expect(title.locator("xpath=ancestor::*[@title][1]")).toHaveAttribute("title", titleText);
    // The sidebar's search box filters the table, so the page shows only that one.
    await expect(page.getByRole("searchbox", { name: /Search listings/ })).toHaveCount(1);
    await page.getByRole("searchbox", { name: /Search listings/ }).fill(titleText);
    await expect(table.getByRole("rowheader")).toHaveCount(1);
    await page.getByRole("searchbox", { name: /Search listings/ }).fill("");
    await page.screenshot({ path: info.outputPath("inventory-table-desktop.png") });
  });

  test("the chats panel shows its whole search placeholder", async ({ page }, info) => {
    // History appears once there is a chat, and opens from the History button.
    expect((await page.request.post("/api/assistant/threads")).ok()).toBe(true);
    await openPage(page, "Assistant");
    await page.getByRole("button", { name: "Show chat history" }).click();
    const search = page.getByRole("searchbox", { name: "Search chats" });
    await expect(search).toBeVisible();
    const placeholder = (await search.getAttribute("placeholder")) ?? "";
    const room = await search.evaluate((element) => element.clientWidth);
    expect(await textWidth(search, placeholder)).toBeLessThanOrEqual(room);
    await page.screenshot({ path: info.outputPath("assistant-desktop.png") });
  });

  test("the eBay tab names eBay once and offers one Connect eBay", async ({ page }, info) => {
    await openPage(page, "Marketplaces");
    const panel = page.getByRole("tabpanel");
    await expect(panel.getByRole("button", { name: "Connect eBay", exact: true })).toHaveCount(1);
    // The tab names the marketplace; the panel's own heading is for screen readers only.
    await expect(panel.getByRole("heading", { name: "eBay", exact: true, level: 2 })).toBeAttached();
    await expect(panel.getByRole("heading", { name: "eBay", exact: true, level: 2 })).toHaveClass(/sr-only/);
    await page.screenshot({ path: info.outputPath("marketplaces-desktop.png") });
  });

  test("every automation row has its switch in the On column", async ({ page }, info) => {
    await openPage(page, "Automations");
    const rows = page.getByRole("region", { name: "Your automations" }).getByRole("listitem");
    await expect(rows.first()).toBeVisible();
    for (const row of await rows.all()) await expect(row.getByRole("switch")).toHaveCount(1);
    await page.screenshot({ path: info.outputPath("automations-desktop.png") });
  });
});

// Screenshots for review at phone size; CI keeps e2e/results.
test("phone screens after the layout fixes", async ({ page, phone }, info) => {
  test.skip(!phone, "Phone screenshots");
  await page.goto("/");
  for (const name of ["Assistant", "Marketplaces", "Automations"]) {
    await page.getByRole("button", { name: "Open navigation" }).first().click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name, exact: true }).click();
    await expect(page.getByRole("main")).toBeVisible();
    await page.screenshot({ path: info.outputPath(`${name.toLowerCase()}-${info.project.name}.png`) });
  }
});

// Safari paints the page canvas under its status bar and behind its floating
// toolbar, so the header, the page and that canvas must be one color.
test("a phone page is one color from the status bar to the toolbar", async ({ page, phone }, info) => {
  test.skip(!phone, "Phone browser chrome");
  for (const scheme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await openPage(page, "Automations", true);
    await expect(page.getByRole("main")).toBeVisible();
    const surfaces = await page.evaluate(() => {
      // Paint backgrounds bottom-up onto one pixel, so translucent layers blend as on screen.
      const paint = (colors: string[]) => {
        const context = document.createElement("canvas").getContext("2d")!;
        for (const color of colors) {
          context.fillStyle = color;
          context.fillRect(0, 0, 1, 1);
        }
        return Array.from(context.getImageData(0, 0, 1, 1).data.slice(0, 3)).join(",");
      };
      // The color the seller sees at a point: every background under it.
      const seen = (x: number, y: number) => {
        const layers: string[] = [];
        for (let element = document.elementFromPoint(x, y); element; element = element.parentElement) {
          layers.unshift(getComputedStyle(element).backgroundColor);
        }
        return paint(layers);
      };
      const header = document.querySelector(".mobile-workspace-bar")!.getBoundingClientRect();
      return {
        canvas: paint([getComputedStyle(document.documentElement).backgroundColor]),
        header: seen(header.left + 2, header.top + 2),
        // The page's left margin halfway down, clear of any card.
        page: seen(2, Math.round(window.innerHeight / 2)),
      };
    });
    expect(surfaces.header, scheme).toBe(surfaces.canvas);
    expect(surfaces.page, scheme).toBe(surfaces.canvas);
    await page.screenshot({ path: info.outputPath(`phone-canvas-${scheme}-${info.project.name}.png`) });
  }
});
