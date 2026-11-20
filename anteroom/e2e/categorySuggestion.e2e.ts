import { createListing, expect, openListing, openForm, test } from "./fixtures";

const GIRLS_SCARVES = "Clothing, Shoes & Accessories > Kids > Girls > Girls' Accessories > Scarves & Wraps";

test("a closest-match category is marked on its tab until the seller confirms it", async ({page, request, phone}, info) => {
  test.setTimeout(120_000);
  const title = `Category suggestion ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    // Poshmark has no girls' scarves, so General's category fills Women > Scarves & Wraps as a suggestion.
    const saved = await request.put(`/api/conversations/${id}/listing`, {data: {expected_revision_id: "", listing: {
      title, description: "Plaid scarf", price: 18, category_path: GIRLS_SCARVES,
    }}});
    expect(saved.ok()).toBe(true);
    const listing = async () => (await (await request.get(`/api/conversations/${id}/listing`)).json()).listing;
    expect((await listing()).direct_category_ids.poshmark_suggested).toBe(true);
    await page.route("**/api/settings/marketplaces", async route => {
      const response = await route.fetch();
      await route.fulfill({response, json: {...await response.json(), selected: ["ebay", "etsy", "poshmark", "mercari", "depop"]}});
    });
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", {name: "Listing"}).click();
    await openForm(page, phone, "Poshmark");
    const row = page.locator('[data-listing-row="direct_category_ids.poshmark"]');
    const hint = row.getByRole("status").filter({hasText: "Suggested: check it."});
    await expect(hint).toBeVisible({timeout: 30_000});
    await row.scrollIntoViewIfNeeded();
    await page.screenshot({path: info.outputPath("category-suggestion.png")});
    await hint.getByRole("button", {name: "Looks right"}).click();
    await expect(hint).toBeHidden();
    await expect.poll(async () => (await listing()).direct_category_ids.poshmark_suggested ?? null).toBeNull();
    // Confirmed on its tab, the category is the seller's: a later General change keeps it.
    expect((await listing()).seller_fields).toContain("direct_category_ids.poshmark");
  } finally { await request.delete(`/api/conversations/${id}`); }
});
