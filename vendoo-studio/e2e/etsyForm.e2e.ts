import { test, expect, createListing, openListing, openForm, titleBox } from "./fixtures";

// Etsy's listing form, tab by tab (skills/list-this/references/etsy-native-form.json).
const SECTIONS = ["Photo & video", "Category", "Item details", "Pricing & shipping", "How it's made", "Settings"];
const TEE = { id: "449", path: "Clothing > Men's Clothing > Shirts & Tees > T-shirts" };

test("the Etsy tab is Etsy's listing form, and its edits stay on Etsy", async ({ page, request, phone }, info) => {
  const title = `Etsy form ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "A soft blue cotton tee.", price: 25, quantity: 1, primaryColor: "Blue",
      weight_oz: 8, package_dimensions_in: "10x8x2",
      direct_category_ids: { etsy: TEE.id, etsy_path: TEE.path },
      etsy_specifics: { whoMade: "Another company or person", whatIsIt: "A finished product", whenMade: "2020 - 2026 (Recently)",
        tags: ["blue tee", "cotton shirt"] },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeVisible();
    await openForm(page, phone, "Etsy");
    const form = page.getByRole("group", { name: "Etsy listing fields" });
    for (const heading of SECTIONS) await expect(form.getByRole("region", { name: heading })).toBeVisible();
    // Only Etsy's own rows: no hand-kept Brand or Condition from the old editor.
    await expect(form.getByText("Brand", { exact: true })).toHaveCount(0);
    await expect(form.getByRole("textbox", { name: "Title", exact: true })).toHaveValue(title);
    await expect(form.getByRole("button", { name: `Category: ${TEE.path.split(" > ").join(" → ")}` })).toBeVisible();
    await expect(form.getByText("Etsy's attributes for this category could not be loaded", { exact: false })).toBeVisible();
    if (process.env.ETSY_FORM_SHOTS) {
      for (const [index, heading] of ["Photo & video", "Item details", "Pricing & shipping", "How it's made"].entries()) {
        await form.getByRole("region", { name: heading }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: `${process.env.ETSY_FORM_SHOTS}/etsy-form-${info.project.name}-${index + 1}.png` });
      }
    }

    // A title typed here is Etsy's; General keeps its own.
    const etsyTitle = form.getByRole("textbox", { name: "Title", exact: true });
    await etsyTitle.fill(`${title} for Etsy`);
    await etsyTitle.blur();
    await expect.poll(async () => {
      const body = await (await request.get(`/api/conversations/${id}/listing`)).json();
      return [body.listing.title, body.listing.etsy_specifics?.title];
    }, { timeout: 15_000 }).toEqual([title, `${title} for Etsy`]);

    // The seller picks Etsy's category on the Etsy tab; the pick is theirs and keeps its breadcrumb.
    await form.getByRole("button", { name: /^Category: / }).click();
    await page.getByRole("combobox", { name: "Search all categories" }).fill("Aprons");
    await page.getByRole("option", { name: /Accessories.*Aprons/ }).first().click();
    await expect.poll(async () => {
      const body = await (await request.get(`/api/conversations/${id}/listing`)).json();
      const ids = body.listing.direct_category_ids ?? {};
      return [ids.etsy, ids.etsy_path, (body.listing.seller_fields ?? []).includes("direct_category_ids.etsy")];
    }, { timeout: 15_000 }).toEqual(["12643", "Accessories > Aprons", true]);
    await expect(form.getByRole("button", { name: "Category: Accessories → Aprons" })).toBeVisible();
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
