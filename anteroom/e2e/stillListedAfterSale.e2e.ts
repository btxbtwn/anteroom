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
  await page.route("**/api/marketplace-sales/still-listed", route => route.fulfill({ json: { autodelist: true, items: [{
    conversationId: id, title, saleId: "sale-1", soldOn: "poshmark", soldAt: "2026-11-14T22:00:00", detected: true, pinned: true,
    stillListed: [
      { marketplace: "ebay", listingId: "377507697747", followed: true, state: "failed", error: "Sign in to eBay again.", at: null, attempts: 2, url: null },
      { marketplace: "mercari", listingId: "m70565754838", followed: true, state: "live", error: null, at: null, attempts: 0, url: null },
    ],
  }] } }));
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
  const alert = page.getByRole("alert").filter({ hasText: "A sold item is still listed" });
  await expect(alert).toBeVisible({ timeout: 45_000 });
  await expect(alert).toContainText("Sold on Poshmark, still on eBay and Mercari (delist failed)");
  await expect(alert).toContainText("Anteroom keeps trying to delist them.");
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
