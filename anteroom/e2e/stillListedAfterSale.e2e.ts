import { expect, test } from "./fixtures";

// The follow-up and its retries are covered by the backend tests; this checks
// what the seller sees while a sold item is still listed elsewhere.
test("a sold item still listed elsewhere is alerted in the sidebar and explained on the item", async ({ page, phone, createConversation }, info) => {
  test.setTimeout(90_000);
  const title = `Still listed fleece ${info.project.name}`;
  const created = await createConversation({ title, notes: "" });
  expect(created.ok()).toBe(true);
  const { id } = await created.json();
  const mutations: string[] = [];
  page.on("request", request => {
    if (/\/marketplace-listings\/delist|\/marketplace-sales\/.+\/dismiss/.test(request.url())) mutations.push(request.url());
  });
  const detected = {
    conversationId: id, title, saleId: "sale-1", soldOn: "poshmark", soldAt: "2026-11-14T22:00:00", detected: true,
    stillListed: [
      { marketplace: "ebay", state: "failed", error: "Sign in to eBay again.", at: null, attempts: 2, url: null },
      { marketplace: "mercari", state: "live", error: null, at: null, attempts: 0, url: null },
    ],
  };
  const manuallySold = Array.from({ length: 3 }, (_, index) => ({
    ...detected, conversationId: `manual-${index}`, saleId: `manual-sale-${index}`,
    title: `In-person item ${index + 1}`, soldOn: "other", detected: false,
  }));
  await page.route("**/api/marketplace-sales/still-listed", route => route.fulfill({
    json: { autodelist: true, items: [...manuallySold, detected] },
  }));
  await page.route(/\/api\/marketplace-sales\?/, route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("conversation_id") !== id) return route.fulfill({ json: { sales: [] } });
    return route.fulfill({ json: { sales: [{
      id: "sale-1", marketplace: "poshmark", orderId: "o1", orderStatus: "paid", listingId: "p1", quantity: 1,
      stockStatus: "sold_out", remainingQuantity: 0, state: "new", soldAt: "2026-11-14T22:00:00", receivedAt: "2026-11-14T22:00:00",
      conversationId: id, title, listedElsewhere: ["ebay", "mercari"], delistErrors: { ebay: "Sign in to eBay again." },
    }] } });
  });

  await page.goto("/");
  if (phone) {
    // On a phone the alert sits at the top of the inventory list.
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  const alert = page.getByRole("alert").filter({ hasText: "4 sold items are still listed" });
  await expect(alert).toBeVisible({ timeout: 45_000 });
  await expect(alert.getByRole("button", { name: /In-person item 1/ })).toContainText("Delist this item under Marketplaces.");
  await expect(alert.getByRole("button", { name: /In-person item 1/ })).not.toContainText("Anteroom retries");
  await expect(alert.getByRole("button", { name: new RegExp(title) })).toHaveCount(0);
  await alert.getByRole("button", { name: "Show 1 more item" }).click();
  await expect(alert).toContainText("Sold on Poshmark, still on eBay and Mercari (delist failed)");
  await expect(alert).toContainText("Anteroom retries eligible delists for up to two weeks after the sale.");
  await page.screenshot({ path: info.outputPath("still-listed-sidebar.png") });

  await alert.getByRole("button", { name: new RegExp(title) }).click();
  // This item has no listing text yet, so a phone opens it on Chat.
  if (phone) await page.getByRole("tablist", { name: "Listing views" }).getByRole("tab", { name: "Listing" }).click();
  const notice = page.getByRole("status").filter({ hasText: "Sold on Poshmark" });
  await expect(notice).toBeVisible({ timeout: 30_000 });
  await expect(notice).toContainText("Anteroom still has listings on eBay and Mercari.");
  await expect(notice).toContainText("did not confirm the delist: Sign in to eBay again.");
  await page.screenshot({ path: info.outputPath("still-listed-item.png") });
  // Seeing the alert never delists or dismisses anything.
  expect(mutations).toEqual([]);
});
