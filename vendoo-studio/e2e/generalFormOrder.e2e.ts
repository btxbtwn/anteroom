import { createListing, expect, openListing, test, titleBox } from "./fixtures";

const SECTIONS = ["Item", "Condition & details", "Pricing", "Description", "Color & department", "Shipping", "More details"];
// The order a seller fills the General form in; screen order must match it.
const ROWS = ["category_path", "title", "condition", "size", "brand", "price", "description",
  "primaryColor", "secondaryColor", "department", "weight_oz", "package_dimensions_in", "zipCode"];

test("General reads in filling order, with More details folded until opened", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const title = `E2E general order ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeVisible();
    const form = page.getByRole("group", { name: "General listing fields" });

    await expect(form.getByRole("heading", { level: 3 })).toHaveText(SECTIONS);

    // Document order (what Tab follows) is the order on screen: top to bottom, then left to right.
    const rows = form.locator("[data-listing-row]");
    expect(await rows.evaluateAll((els) => els.map((el) => (el as HTMLElement).dataset.listingRow)))
      .toEqual([...ROWS, "cost", "sku", "quantity"]);
    const boxes = [];
    for (const key of ROWS) boxes.push((await form.locator(`[data-listing-row="${key}"]`).boundingBox())!);
    for (let index = 1; index < boxes.length; index++) {
      const [before, after] = [boxes[index - 1]!, boxes[index]!];
      const sameRow = Math.abs(before.y - after.y) < 4;
      expect(sameRow ? after.x > before.x : after.y > before.y, `${ROWS[index]} follows ${ROWS[index - 1]}`).toBe(true);
    }
    // Phones get one column.
    if (phone) expect(Math.abs(boxes[2]!.x - boxes[3]!.x)).toBeLessThan(1);

    const more = form.getByRole("button", { name: "More details" });
    await expect(more).toHaveAttribute("aria-expanded", "false");
    const sku = form.locator("#listing-field-sku");
    await expect(sku).toBeHidden();
    await more.click();
    await expect(more).toHaveAttribute("aria-expanded", "true");
    await expect(sku).toBeVisible();
    await sku.fill("E2E-SKU-1");
    await sku.blur();
    // The edit saves: it is there after reopening the listing, folded again.
    await expect.poll(async () => {
      const rows = await (await request.get("/api/conversations")).json() as { id: string; sku: string | null }[];
      return rows.find((row) => row.id === id)?.sku ?? null;
    }, { timeout: 15_000 }).toBe("E2E-SKU-1");
    await openListing(page, phone, title);
    await titleBox(page, phone);
    await expect(more).toHaveAttribute("aria-expanded", "false");
    await more.click();
    await expect(sku).toHaveValue("E2E-SKU-1");
    await page.screenshot({ path: info.outputPath("general-more-details-open.png") });
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
