import { createListing, expect, openListing, test, titleBox } from "./fixtures";

test("a marketplace rejection leaves the form editable and List available for retry", async ({ page, phone, request }, info) => {
  const title = `Marketplace rejection ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const category = "01008c10d97b4e1245005764/04008c10d97b4e1245005764/18009813d97b4e3995005764";
  let attempts = 0;
  let rejected = false;
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Private test", price: 25, quantity: 1, direct_category_ids: { poshmark: category },
      poshmark_specifics: { multiItem: false, sizeSystem: "us", inventoryRows: [
        { sizeSetId: "custom", sizeId: "Old size", quantity: 1, sku: "" },
      ] },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    const status = () => rejected ? { poshmark: { state: "not_saved", running: false, error_code: "missing_fields",
      error: "Poshmark rejected the size.", marketplace_message: "Please select a valid size.",
      error_fields: [{ field: "poshmark_specifics.inventoryRows", message: "Please select a valid size." }] } } : {};
    // No marketplace mutation leaves the browser. The sandbox also refuses real publication.
    await page.route(`**/api/conversations/${id}/native-marketplaces**`, async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/preflight")) {
        await route.fulfill({ json: { revision_id: (await saved.json()).current_revision_id, marketplaces: {
          poshmark: { ready: false, gaps: [{ field: "poshmark_specifics.inventoryRows", message: "Size needs review.", blocking: true }],
            missing: [{ field: "poshmark_specifics.inventoryRows", label: "Size", message: "Size needs review." }] },
        } } });
      } else if (path.endsWith("/poshmark/list")) {
        expect(route.request().method()).toBe("POST");
        expect(route.request().postDataJSON().confirm).toBe(true);
        attempts += 1;
        rejected = true;
        await route.fulfill({ json: status().poshmark });
      } else if (path.endsWith("/native-marketplaces")) {
        await route.fulfill({ json: status() });
      } else await route.continue();
    });
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeVisible();
    await page.getByRole("navigation", { name: "Marketplaces" }).getByRole("button", { name: /^Poshmark Poshmark/ }).click();
    const list = page.getByRole("button", { name: "List…", exact: true });
    await expect(list).toBeEnabled();
    await list.click();
    await page.getByRole("button", { name: "List on Poshmark", exact: true }).click();
    await expect(page.getByText(/Poshmark said: Please select a valid size/)).toBeVisible();
    await page.screenshot({ path: info.outputPath("marketplace-rejection.png"), fullPage: true });
    const size = page.getByRole("textbox", { name: "Custom size 1", exact: true });
    await expect(size).toBeEditable();
    await size.fill("Custom M");
    await size.blur();
    await expect.poll(async () => (await (await request.get(`/api/conversations/${id}/listing`)).json())
      .listing.poshmark_specifics.inventoryRows[0].sizeId).toBe("Custom M");
    await expect(list).toBeEnabled();
    await list.click();
    await page.getByRole("button", { name: "List on Poshmark", exact: true }).click();
    await expect.poll(() => attempts).toBe(2);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
