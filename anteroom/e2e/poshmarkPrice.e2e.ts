import { createListing, expect, openForm, openListing, test, titleBox } from "./fixtures";

test("Poshmark shows a field error for cents and clears it for whole dollars", async ({ page, phone, request }, info) => {
  const title = `Poshmark price check ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "A test jacket", price: 12, quantity: 1,
      direct_category_ids: { poshmark: "01008c10d97b4e1245005764/04008c10d97b4e1245005764/18009813d97b4e3995005764" },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeVisible();
    await openForm(page, phone, "Poshmark");
    const form = page.getByRole("group", { name: "Poshmark listing fields" });
    const price = form.getByRole("spinbutton", { name: /Listing Price/ });
    const error = form.locator("[data-listing-row='poshmark_specifics.poshmarkPrice']").getByRole("alert")
      .filter({ hasText: "Enter a whole-dollar price for Poshmark." });
    await expect(price).toHaveValue("12");

    await price.fill("11.70");
    await price.blur();
    await expect(error).toBeVisible();

    await price.fill("12");
    await price.blur();
    await expect.poll(async () => {
      const response = await request.get(`/api/conversations/${id}/listing`);
      return (await response.json()).listing.poshmark_specifics?.poshmarkPrice;
    }).toBe(12);
    await expect(error).toHaveCount(0);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
