import { createListing, expect, openForm, openListing, test } from "./fixtures";

test("Mercari defaults to No brand and saves the seller's checkbox choice", async ({ page, request, phone }, info) => {
  const title = `Mercari brand ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const listingUrl = `/api/conversations/${id}/listing`;
  try {
    const saved = await request.put(listingUrl, { data: { expected_revision_id: "", listing: {
      title, description: "Manual draft for Mercari", price: 25, brand: "Unlisted Brand for Sandbox",
    } } });
    expect(saved.ok()).toBe(true);
    await page.route("**/api/settings/marketplaces", async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), selected: ["mercari"] } });
    });
    await openListing(page, phone, title);
    await openForm(page, phone, "Mercari");

    const checkbox = page.getByRole("checkbox", { name: "No brand / Not sure" });
    await expect(checkbox).toBeChecked();
    if (process.env.MERCARI_SCREENSHOT_DIR) {
      await checkbox.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${process.env.MERCARI_SCREENSHOT_DIR}/mercari-no-brand-${info.project.name}.png` });
    }

    const specifics = async () => (await (await request.get(listingUrl)).json()).listing.mercari_specifics;
    await checkbox.click();
    await expect(checkbox).not.toBeChecked();
    await expect.poll(async () => (await specifics()).noBrand).toBe(false);
    await checkbox.click();
    await expect(checkbox).toBeChecked();
    await expect.poll(async () => (await specifics()).noBrand).toBe(true);

    await page.reload();
    await openListing(page, phone, title);
    await openForm(page, phone, "Mercari");
    await expect(checkbox).toBeChecked();
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
