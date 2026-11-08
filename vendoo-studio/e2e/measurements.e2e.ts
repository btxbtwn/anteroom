import { createListing, expect, openListing, test } from "./fixtures";

test("a single listing reads its measurements and saves them only on Fill in", async ({ page, phone, request }, info) => {
  const title = `Measurements ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, {
      data: { listing: { title }, expected_revision_id: "" },
    });
    expect(saved.ok()).toBe(true);
    // The sandbox has no photo model, so the reading itself is stubbed; the rest is the real app.
    const reads: string[] = [];
    await page.route("**/api/conversations/*/measurements/read", async (route) => {
      reads.push(route.request().postDataJSON().garment);
      await route.fulfill({
        json: {
          photos_read: 3,
          measurements: {
            pitToPit: { status: "accepted", value: 21.5, reads: [21.5, 21.5] },
            length: { status: "accepted", value: 28, reads: [28, 28.125] },
            sleeve: { status: "disagree", reads: [8, 9] },
          },
        },
      });
    });
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    const section = page.getByRole("region", { name: "Measurements (in)" });
    await section.getByRole("radio", { name: "Top" }).click();
    const pitToPit = section.getByRole("spinbutton", { name: "Pit to Pit" });
    for (const name of ["Pit to Pit", "Length", "Sleeve"]) await section.getByRole("spinbutton", { name }).fill("");

    await section.getByRole("button", { name: "Read measurements" }).click();
    const suggestion = section.getByRole("status").filter({ hasText: "From the photos" });
    await expect(suggestion).toHaveText(/From the photos: Pit to Pit 21\.5 in, Length 28 in\..*Measure by hand: .*Sleeve/);
    expect(reads).toEqual(["top"]);
    await expect(pitToPit).toHaveValue("");

    await suggestion.getByRole("button", { name: "Fill in" }).click();
    await expect(pitToPit).toHaveValue("21.5");
    await expect(section.getByRole("spinbutton", { name: "Length" })).toHaveValue("28");
    await expect(suggestion).toBeHidden();
  } finally {
    const removed = await request.delete(`/api/conversations/${id}`);
    expect(removed.ok()).toBe(true);
  }
});
