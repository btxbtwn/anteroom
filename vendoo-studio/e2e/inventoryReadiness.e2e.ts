import { createListing, expect, test } from "./fixtures";

test("the inventory table counts required fields a listing has not filled yet, before anything is listed", async ({ page, phone, request }, info) => {
  const title = `Readiness ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    // No price: Depop's form requires one.
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Private test", quantity: 1, direct_category_ids: { depop: "menswear_tshirts" },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    const readiness = await (await request.get(`/api/listing-readiness?ids=${id}`)).json();
    const depop = readiness.listings[id].marketplaces.depop.missing.map((item: { label: string }) => item.label);
    expect(depop).toEqual(expect.arrayContaining(["Item price"]));

    await page.goto("/");
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    }
    await page.getByRole("button", { name: "Inventory table" }).click();
    await page.getByRole("searchbox", { name: "Search listings by title, SKU or label" }).fill(title);
    const table = page.getByRole("table", { name: "Inventory" });
    const cell = table.getByRole("button", { name: new RegExp(`on Depop: Needs ${depop.length} fields?: .*Item price`) });
    await expect(cell).toBeVisible({ timeout: 20_000 });
    await expect(cell).toContainText(`Needs ${depop.length} field`);
    await page.screenshot({ path: info.outputPath("inventory-readiness.png") });
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
