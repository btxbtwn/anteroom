import { createListing, expect, openForm, openListing, test } from "./fixtures";

test("editing and clearing the minimum price keeps legacy Smart Pricing on", async ({ page, request, phone }, info) => {
  test.setTimeout(60_000);
  const title = `Mercari Smart Pricing ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const listingUrl = `/api/conversations/${id}/listing`;
  try {
    const initial = await request.put(listingUrl, { data: { expected_revision_id: "", listing: {
      title, price: 40, mercari_specifics: { smartPricing: true, shippingMethod: "SOYO" },
    } } });
    expect(initial.ok(), await initial.text()).toBe(true);
    await page.route("**/api/settings/marketplaces", async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), selected: ["mercari"] } });
    });
    await openListing(page, phone, title);
    await openForm(page, phone, "Mercari");

    const toggle = page.locator('[data-listing-field="mercari_specifics.smartPricing.enabled"]');
    const minimum = page.locator('[data-listing-field="mercari_specifics.smartPricing.minimumPrice"]');
    await expect(toggle).toBeChecked();
    await expect(minimum).toHaveValue("34");
    await minimum.fill("21.25");
    await minimum.blur();
    const savedPricing = async () => (await (await request.get(listingUrl)).json()).listing.mercari_specifics.smartPricing;
    await expect.poll(savedPricing).toEqual({ enabled: true, minimumPrice: 21.25 });
    await expect(toggle).toBeChecked();
    await expect(minimum).toHaveValue("21.25");
    const screenshotDir = process.env.MERCARI_SCREENSHOT_DIR;
    if (process.env.CI || screenshotDir) {
      await minimum.scrollIntoViewIfNeeded();
      const image = await page.screenshot({ path: screenshotDir && `${screenshotDir}/mercari-smart-pricing-${info.project.name}.png` });
      await info.attach("Mercari Smart Pricing after saving a manual minimum", { body: image, contentType: "image/png" });
    }

    await minimum.fill("");
    await minimum.blur();
    await expect.poll(savedPricing).toEqual({ enabled: true, minimumPrice: null });
    await expect(toggle).toBeChecked();
    await expect(minimum).toHaveValue("");
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
