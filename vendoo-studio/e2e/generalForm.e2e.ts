import { createListing, expect, openListing, test, titleBox } from "./fixtures";

test("the General form keeps its category-first order and quiet field tools", async ({ page, request, phone }, info) => {
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
    await expect(titleInput).toBeVisible({ timeout: 30_000 });
    const form = page.getByRole("group", { name: "General listing fields" });

    // Preserve the category-first order from #1342; private notes come last.
    await expect(form.getByRole("heading")).toHaveText([
      "Item", "Condition & details", "Pricing", "Description", "Color & department", "Measurements (in)", "Shipping", "More details", "Notes & Labels",
    ]);
    // Kept with CI's browser report, for reviewing the layout at desktop and phone sizes.
    await page.screenshot({ path: info.outputPath("general-top.png") });

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
    await page.screenshot({ path: info.outputPath("general-title-focused.png") });
    await measurements.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("general-measurements.png") });

    if (!phone) {
      // The rail says Etsy's state in full; the listing check below gives the reason once.
      const etsy = page.getByRole("navigation", { name: "Marketplaces" }).getByRole("listitem", { name: "Etsy" });
      const status = etsy.getByRole("status");
      await expect(status).toHaveText("May not qualify");
      expect(await status.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
      await expect(etsy.getByRole("button").first()).toHaveAttribute("title", "May not qualify");
    }
  } finally { await request.delete(`/api/conversations/${id}`); }
});
