import { createListing, expect, openListing, test, titleBox } from "./fixtures";

test("manual General edits reach every marketplace and survive reload without generation", async ({page, request, phone}, info) => {
  test.setTimeout(150_000);
  const title = `Manual General ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, {data: {expected_revision_id: "", listing: {
      title, description: "Manual draft", price: 25,
    }}});
    expect(saved.ok()).toBe(true);
    await page.route("**/api/settings/marketplaces", async route => {
      const response = await route.fetch();
      await route.fulfill({response, json: {...await response.json(), selected: ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted"]}});
    });
    await openListing(page, phone, title);
    const input = await titleBox(page, phone);
    await input.fill("Manually corrected title");
    await input.blur();
    await expect.poll(async () => (await (await request.get(`/api/conversations/${id}/listing`)).json()).listing.title).toBe("Manually corrected title");
    for (const market of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop", "Sellwild", "Vinted"]) {
      await page.getByRole("navigation", {name: "Marketplaces"}).getByRole("button", {name: new RegExp(`^${market} ${market}`)}).click();
      const form = page.getByRole("group", {name: `${market} listing fields`});
      if (market === "Depop") await expect(form.getByRole("textbox", {name: /description/i}).first()).toHaveValue(/Manual draft/, {timeout: 30000});
      else await expect(form.getByRole("textbox", {name: market === "Sellwild" ? /What are you selling/ : /title/i}).first()).toHaveValue("Manually corrected title", {timeout: 30000});
    }
    await page.reload();
    await openListing(page, phone, "Manually corrected title");
    await expect(await titleBox(page, phone)).toHaveValue("Manually corrected title");
  } finally { await request.delete(`/api/conversations/${id}`); }
});
