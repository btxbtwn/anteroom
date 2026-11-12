import { test, expect, createListing, openListing, openForm, titleBox } from "./fixtures";

test("reload marketplace fields preserves answers and refreshes validation", async ({ page, request, phone }, info) => {
  test.setTimeout(90_000);
  const title = `Reload fields ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Seller's description", price: 25,
      direct_category_ids: { mercari: "2008" },
      mercari_specifics: { title: "Seller's Mercari title", shippingMethod: "SOYO" },
      _native_forms: { mercari: { category_id: "old", page_labels: true,
        inputs: { "direct_category_ids.mercari": "old" },
        fields: [{ path: "direct_category_ids.mercari", label: "Category" }] } },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    const before = await (await request.get(`/api/conversations/${id}/native-marketplaces/preflight`)).json();
    expect(before.marketplaces.mercari.gaps.some((gap: { message: string }) => gap.message.includes("Reload its fields"))).toBe(true);
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeVisible();
    await openForm(page, phone, "Mercari");
    const form = page.getByRole("group", { name: "Mercari listing fields" });
    const reload = form.getByRole("button", { name: "Reload fields", exact: true });
    await expect(reload).toBeEnabled();
    // Hosted saves reject writes while chat owns the draft. The retained edit
    // must recover automatically and release Reload fields afterwards.
    let refused = false;
    await page.route(`**/api/conversations/${id}/listing`, async route => {
      if (route.request().method() === "PUT" && !refused) {
        refused = true;
        await route.fulfill({ status: 409, contentType: "application/json",
          headers: { "X-Anteroom-Reason": "draft_busy" },
          body: JSON.stringify({ detail: "Wait for the draft task to finish, or stop it before editing." }) });
      } else await route.continue();
    });
    const titleInput = form.getByRole("textbox", { name: "Title", exact: true });
    await titleInput.fill("Seller's updated Mercari title");
    await titleInput.blur();
    await expect.poll(() => refused).toBe(true);
    await expect.poll(async () => (await (await request.get(`/api/conversations/${id}/listing`)).json()).listing.mercari_specifics.title)
      .toBe("Seller's updated Mercari title");
    await expect(reload).toBeEnabled();
    const refreshed = page.waitForResponse(response => response.url().endsWith(`/native-marketplaces/mercari/form/refresh`));
    await reload.click();
    expect((await refreshed).ok()).toBe(true);
    await expect(reload).toBeEnabled();
    await expect.poll(async () => {
      const body = await (await request.get(`/api/conversations/${id}/listing`)).json();
      return [body.listing.title, body.listing.mercari_specifics.title,
        body.listing._native_forms.mercari.inputs["direct_category_ids.mercari"]];
    }).toEqual([title, "Seller's updated Mercari title", "2008"]);
    const after = await (await request.get(`/api/conversations/${id}/native-marketplaces/preflight`)).json();
    expect(after.marketplaces.mercari.gaps.some((gap: { message: string }) => gap.message.includes("Reload its fields"))).toBe(false);
    await expect(form.getByRole("textbox", { name: "Title", exact: true })).toHaveValue("Seller's updated Mercari title");
    await expect(form.getByText("The form's category or dependent choices changed.", { exact: false })).toHaveCount(0);
    await page.screenshot({ path: info.outputPath("reloaded-fields.png") });
    for (const market of ["eBay", "Poshmark", "Depop", "Etsy"]) {
      await openForm(page, phone, market);
      await expect(page.getByRole("button", { name: "Reload fields", exact: true })).toBeEnabled();
    }
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
