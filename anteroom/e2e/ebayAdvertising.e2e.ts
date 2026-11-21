import { expect, test } from "./fixtures";

test("review all active eBay listings before promoting them", async ({ page, phone }, info) => {
  const listings = [
    { id: "123456789012", title: "Wool coat", fields: {}, children: [] },
    { id: "123456789013", title: "Denim jeans", fields: {}, children: [] },
  ];
  const writes: string[] = [];
  const payload = { campaign_id: "campaign-1", listing_ids: listings.map(({ id }) => id), bid_percent: "3.5" };
  await page.route(/\/api\/updates(?:\/progress)?$/, route => route.fulfill({ json: { available: false, status: "idle" } }));
  await page.route("**/api/marketplace-accounts**", route => route.fulfill({ json: {
    accounts: { ebay: { connected: true, label: "Sample seller", connectedAt: 1, pending: false } },
    relay: { running: false, connected: false },
  } }));
  await page.route("**/api/ebay/read/**", route => {
    const section = new URL(route.request().url()).pathname.split("/").at(-1);
    const rows = section === "advertising-listings" ? listings : section === "campaigns"
      ? [{ id: "campaign-1", title: "General campaign", status: "RUNNING", fields: {}, children: [] }] : [];
    return route.fulfill({ json: { rows } });
  });
  await page.route("**/api/ebay/actions/preview", route => {
    expect(route.request().postDataJSON()).toEqual({ kind: "ad", payload });
    return route.fulfill({ json: { id: "review-1", summary: [
      "Promote 2 selected listings in General campaign", "Ad rate: 3.5% of attributed sales", ...listings.map(({ title }) => title),
    ], expiresAt: new Date(Date.now() + 60_000).toISOString() } });
  });
  await page.route("**/api/ebay/actions/confirm", route => {
    writes.push(route.request().url());
    expect(route.request().postDataJSON()).toEqual({ id: "review-1", kind: "ad", payload, confirm: true });
    return route.fulfill({ json: { state: "sent", id: "review-1", message: "eBay added 2 of 2 listings to the campaign." } });
  });

  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Marketplaces", exact: true }).click();
  await page.getByRole("tab", { name: "Marketing", exact: true }).click();
  await page.getByRole("tab", { name: "Advertising", exact: true }).click();
  await page.getByRole("searchbox", { name: "Filter listings" }).fill("Wool");
  await page.getByRole("button", { name: "Select all active listings" }).click();
  await expect(page.getByText("2 selected", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Select General campaign" }).click();
  await page.getByRole("button", { name: "Promote selected listings", exact: true }).click();
  await page.getByRole("spinbutton", { name: "Ad rate (%)", exact: true }).fill("3.5");
  await page.getByRole("button", { name: "Review promote selected listings" }).click();
  const review = page.getByRole("dialog", { name: "Promote selected listings" });
  await expect(review).toContainText("3.5% of attributed sales");
  await expect(review).toContainText("Denim jeans");
  expect(writes).toHaveLength(0);
  await page.screenshot({ path: info.outputPath("ebay-bulk-promotion.png") });
  await review.getByRole("button", { name: "Confirm promote selected listings" }).click();
  await expect(page.getByText("eBay added 2 of 2 listings to the campaign.")).toBeVisible();
  expect(writes).toHaveLength(1);
});
