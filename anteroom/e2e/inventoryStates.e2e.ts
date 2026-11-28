import type { Page } from "@playwright/test";
import { createListing, expect, test } from "./fixtures";

async function openInventoryOverview(page: Page, phone: boolean) {
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await page.getByRole("button", { name: "Inventory table" }).click();
}

test("inventory load failures show the reason and recover on Retry", async ({ page, phone }) => {
  let failLoad = true;
  await page.route("**/api/conversations", async (route) => {
    if (route.request().method() === "GET" && failLoad) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Inventory store temporarily busy" }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/");
  await openInventoryOverview(page, phone);

  const alert = page.getByRole("alert").filter({ hasText: "Inventory store temporarily busy" });
  await expect(alert).toBeVisible({ timeout: 15_000 });
  const retry = alert.getByRole("button", { name: "Retry" });
  await expect(retry).toBeVisible();

  // The same error alert with Retry also appears on marketplace sheet tabs.
  await page.getByRole("tab", { name: "Depop" }).click();
  await expect(alert).toBeVisible();
  await expect(alert.getByRole("button", { name: "Retry" })).toBeVisible();

  failLoad = false;
  await alert.getByRole("button", { name: "Retry" }).click();
  await expect(alert).toHaveCount(0, { timeout: 15_000 });
});

test("inventory overview stacks without sideways scrolling on phones and shows price inconsistency in a popover", async ({ page, phone, request }) => {
  const title = `Drift Check ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await page.route("**/api/conversations", async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      const response = await route.fetch();
      const rows = (await response.json()) as Array<Record<string, unknown>>;
      const patched = rows.map((row) =>
        row.id === id
          ? {
              ...row,
              status: "active",
              price: 55,
              price_drift: [
                { marketplace: "poshmark", price: 55, expected: 64 },
                { marketplace: "depop", price: 50, expected: 58 },
              ],
            }
          : row,
      );
      await route.fulfill({ response, json: patched });
    });

    await page.goto("/");
    await openInventoryOverview(page, phone);

    const search = page.getByRole("searchbox", { name: /Search listings/ });
    await search.fill(title);

    if (phone) {
      const list = page.getByRole("list", { name: "Inventory" });
      await expect(list).toBeVisible();
      await expect(page.getByRole("table", { name: "Inventory" })).toHaveCount(0);
      const overflow = await list.evaluate((el) => el.scrollWidth - el.clientWidth);
      expect(overflow).toBeLessThanOrEqual(1);
    } else {
      await expect(page.getByRole("table", { name: "Inventory" })).toBeVisible();
    }

    const trigger = page.getByRole("button", { name: "Price inconsistency" }).first();
    await expect(trigger).toBeVisible();
    await trigger.click();

    const popup = page.locator('[data-slot="popover-popup"]');
    await expect(popup).toBeVisible();
    await expect(popup).toContainText("Poshmark");
    await expect(popup).toContainText("$55");
    await expect(popup).toContainText("$64");
    await expect(popup).toContainText("Depop");
    await expect(popup).toContainText("$58");
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
