import { createListing, expect, openListing, test, titleBox } from "./fixtures";

test("the General form leads with title, description and price, and keeps field tools quiet", async ({ page, request, phone }, info) => {
  test.setTimeout(150_000);
  const title = `General order ${info.project.name} ${Date.now()} skinny jeans`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Black high rise skinny jeans.", price: 38, brand: "Madewell",
      category_path: "Clothing, Shoes & Accessories > Women > Women's Clothing > Jeans",
    } } });
    expect(saved.ok()).toBe(true);
    await page.route("**/api/settings/marketplaces", async route => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), selected: ["ebay", "etsy", "poshmark", "mercari", "depop"] } });
    });
    await openListing(page, phone, title);
    const titleInput = await titleBox(page, phone);
    const form = page.getByRole("group", { name: "General listing fields" });

    // Buyers see title, description and price first; the seller's private notes come last.
    await expect(form.getByRole("heading")).toHaveText([
      "Listing", "Pricing", "Item", "Condition & details", "Measurements (in)", "Shipping", "Inventory", "Notes & Labels",
    ]);

    // Jeans are measured as pants, and empty measurements look empty.
    const measurements = form.getByRole("region", { name: "Measurements (in)" });
    await expect(measurements.getByRole("radio", { name: "Pants" })).toHaveAttribute("aria-checked", "true");
    for (const input of await measurements.getByRole("spinbutton").all()) await expect(input).not.toHaveAttribute("placeholder", /\d/);

    // Ask chat waits behind the field until the seller is in it, and stays one Tab away.
    const ask = form.getByRole("button", { name: "Ask chat to fill Title" });
    const tools = ask.locator("..");
    await expect(tools).toHaveCSS("opacity", "0");
    await expect(form.getByText("Ask chat", { exact: true })).toHaveCount(0);
    await titleInput.focus();
    await expect(tools).toHaveCSS("opacity", "1");

    if (!phone) {
      // The rail says Etsy's state in full and puts the reason in the row's tooltip.
      const etsy = page.getByRole("navigation", { name: "Marketplaces" }).getByRole("listitem", { name: "Etsy" });
      const status = etsy.getByRole("status");
      await expect(status).toHaveText("May not qualify");
      expect(await status.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
      await expect(etsy.getByRole("button").first()).toHaveAttribute("title", /^May not qualify: Etsy generally requires/);
    }
  } finally { await request.delete(`/api/conversations/${id}`); }
});
