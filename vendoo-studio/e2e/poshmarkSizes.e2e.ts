import { test, expect, createListing, openListing, openForm, titleBox } from "./fixtures";

// A real category/group from the bundled Poshmark form, with XS/S/M/L choices.
const category = "01008c10d97b4e1245005764/04008c10d97b4e1245005764/18009813d97b4e3995005764";
const standard = "03003667d97b4e6bdb005784";

for (const change of ["size", "system", "custom size during refresh"]) test(`correcting a Poshmark ${change} saves the actual selected group ID`, async ({ page, request, phone }, info) => {
  const custom = change === "custom size during refresh";
  const title = `Poshmark size correction ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "A test jacket", price: 25, quantity: 1,
      direct_category_ids: { poshmark: category },
      poshmark_specifics: { multiItem: false, sizeSystem: "us", inventoryRows: [
        { sizeSetId: custom ? "custom" : "Standard", sizeId: custom ? "Old custom" : change === "size" ? "XS" : "M", quantity: 1, sku: "" },
      ] },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    let refresh = 0;
    if (custom) await page.route(`**/api/conversations/${id}/activity`, async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), message_count: ++refresh } });
    });
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeVisible();
    await openForm(page, phone, "Poshmark");
    if (custom) {
      const size = page.getByRole("textbox", { name: "Custom size 1", exact: true });
      await size.fill("Custom M");
      const updated = await page.waitForResponse(response => new URL(response.url()).pathname === `/api/conversations/${id}/activity`);
      await updated.finished();
      // Let React commit the query update without moving focus out of Size.
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      await expect(size).toHaveValue("Custom M");
      await size.blur();
    } else if (change === "size") {
      await page.locator("#inventory-row-0-size").fill("M");
      await page.getByRole("option", { name: "M", exact: true }).click();
    } else {
      await page.getByRole("tablist", { name: "Sizing", exact: true }).getByRole("tab", { name: "AU", exact: true }).click();
    }
    await expect.poll(async () => {
      const response = await request.get(`/api/conversations/${id}/listing`);
      const body = await response.json();
      return body.listing.poshmark_specifics?.inventoryRows?.[0];
    }, { timeout: 15_000 }).toMatchObject({ sizeSetId: custom ? "custom" : standard, sizeId: custom ? "Custom M" : change === "size" ? "M" : "MAU" });
    const checked = await (await request.get(`/api/conversations/${id}/native-marketplaces/preflight`)).json();
    expect(checked.marketplaces.poshmark.gaps.filter((gap: { field: string }) => gap.field.includes("inventoryRows"))).toEqual([]);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
