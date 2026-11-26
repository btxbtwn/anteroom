import { createListing, expect, openForm, openListing, test } from "./fixtures";

test("eBay form saves the campaign and rate from an explicit opt-in", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const title = `Promotion demo ${info.project.name}`;
  const id = await createListing(request, title);
  try {
    const url = `/api/conversations/${id}/listing`;
    const saved = await request.put(url, { data: { expected_revision_id: "", listing: {
      title, description: "Sample listing for the offline sandbox.", price: 25, quantity: 1, weight_oz: 8,
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    await page.route(url => url.pathname.endsWith(`/${id}/native-marketplaces/ebay/form`), async route => {
      const response = await route.fetch();
      const json = await response.json();
      const campaign = json.fields.find((row: { key: string }) => row.key === "ebay_specifics.promotion.campaignId");
      campaign.options = ["General campaign"];
      campaign.option_values = { "General campaign": "42" };
      campaign.help = "";
      const toggle = json.fields.find((row: { key: string }) => row.key === "ebay_specifics.promotion.enabled");
      toggle.help = "The ad fee is charged at your rate on attributed sales.";
      await route.fulfill({ response, json });
    });
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    await openForm(page, phone, "eBay");
    const toggle = page.getByRole("switch", { name: "Promoted Listings" });
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    const rate = page.getByRole("spinbutton", { name: "Ad rate (%)" });
    await rate.fill("5.5");
    const campaign = page.getByRole("combobox", { name: "Select campaign" });
    await campaign.click();
    await page.getByRole("option", { name: "General campaign" }).click();
    await expect.poll(async () => {
      const doc = await (await request.get(url)).json();
      const promotion = doc.listing.ebay_specifics?.promotion;
      return promotion && { ...promotion, adRate: String(promotion.adRate) };
    }).toMatchObject({ enabled: true, campaignId: "42", adRate: "5.5" });
    await rate.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("ebay-form-promotion.png") });
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
