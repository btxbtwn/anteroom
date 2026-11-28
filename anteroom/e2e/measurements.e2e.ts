import { createListing, expect, openForm, openListing, test } from "./fixtures";

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

test("a Depop draft keeps its description measurements without an unsupported-field warning", async ({ page, phone, request }, info) => {
  const title = `Blue western shirt ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const notes = JSON.stringify({ garment: "top", measurements: { top: { pitToPit: "22", length: "31" } } });
    const details = await request.patch(`/api/conversations/${id}`, { data: { notes } });
    expect(details.ok(), await details.text()).toBe(true);
    const description = 'Blue western shirt.\n\nMeasurements: Pit to pit: 22", Length: 31"';
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description, brand: "Wrangler", condition: "Good", size: "M", price: 24, quantity: 1,
      primaryColor: "Blue", direct_category_ids: { depop: "menswear_shirts" }, depop_specifics: { parcelSize: "Small" },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    const { revision_id } = await saved.json();
    const refreshed = await request.post(`/api/conversations/${id}/native-marketplaces/depop/form/refresh`, { data: { revision_id } });
    expect(refreshed.ok(), await refreshed.text()).toBe(true);

    const preflightPath = `/api/conversations/${id}/native-marketplaces/preflight`;
    await page.route(url => url.pathname === preflightPath, async route => {
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBe(true);
      const result = await response.json();
      const depop = result.marketplaces.depop;
      // Isolate the measurements from the category and occasion aliases seeded by a manual fixture save.
      const fixtureAliases = ["depop_specifics.categoryId", "depop_specifics.occasion"];
      depop.gaps = depop.gaps.filter((gap: { field: string }) => !fixtureAliases.includes(gap.field));
      await route.fulfill({ response, json: result });
    });
    const preflight = page.waitForResponse(response => new URL(response.url()).pathname === preflightPath);
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing", exact: true }).click();
    await openForm(page, phone, "Depop");
    const result = await (await preflight).json();
    expect(result.marketplaces.depop.gaps).toEqual([]);
    await expect(page.getByRole("textbox", { name: "Description" })).toHaveValue(description);
    await expect(page.getByText("This field is not provided by the selected marketplace form.", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: info.outputPath("depop-measurements.png") });
  } finally {
    const removed = await request.delete(`/api/conversations/${id}`);
    expect(removed.ok()).toBe(true);
  }
});


test("a Depop draft shows jeans measurements and saves a centimeter edit", async ({ page, phone, request }, info) => {
  const title = `Light wash jeans ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const notes = JSON.stringify({ garment: "pants", measurements: { pants: { waist: "14", inseam: "28" } } });
    expect((await request.patch(`/api/conversations/${id}`, { data: { notes } })).ok()).toBe(true);
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Light wash jeans.", brand: "Wild Fable", condition: "Good", size: "0", price: 10.8,
      direct_category_ids: { depop: "womenswear_jeans" }, depop_specifics: { parcelSize: "Large" },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing", exact: true }).click();
    await openForm(page, phone, "Depop");
    const waist = page.locator('input[data-listing-field="depop_specifics.measurementInputs.waist"]');
    await expect(waist).toHaveValue("14 in");
    await expect(page.locator('input[data-listing-field="depop_specifics.measurementInputs.inseam"]')).toHaveValue("28 in");
    const outseam = page.locator('input[data-listing-field="depop_specifics.measurementInputs.outseam"]');
    await outseam.fill("91.5 cm");
    await outseam.blur();
    await expect.poll(async () => {
      const current = await (await request.get(`/api/conversations/${id}/listing`)).json();
      return current.listing.depop_specifics?.measurementInputs?.outseam;
    }).toBe("91.5 cm");
    await waist.evaluate(element => element.scrollIntoView({ block: "center" }));
    await page.screenshot({ path: info.outputPath("jeans-measurements.png") });
  } finally {
    expect((await request.delete(`/api/conversations/${id}`)).ok()).toBe(true);
  }
});
