import { test, expect, createListing, openListing, openForm, titleBox } from "./fixtures";

test("Reload fields appears only to retry a failed automatic reload, and keeps answers", async ({ page, request, phone }, info) => {
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
    // Automatic reloads fail until Mercari answers again; only then is Reload fields offered.
    let mercariAnswers = false;
    let refusedReloads = 0;
    await page.route(`**/api/conversations/${id}/native-marketplaces/mercari/form/refresh`, async route => {
      if (!mercariAnswers) {
        refusedReloads += 1;
        await route.fulfill({ status: 500, contentType: "application/json",
          body: JSON.stringify({ detail: "Mercari did not answer." }) });
      } else await route.continue();
    });
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeVisible();
    await openForm(page, phone, "Mercari");
    const form = page.getByRole("group", { name: "Mercari listing fields" });
    const reload = form.getByRole("button", { name: "Reload fields", exact: true });
    await expect(form.getByText("Mercari fields could not be reloaded", { exact: false })).toBeVisible();
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
    // The saved edit is a new revision; its automatic reload fails the same way.
    await expect.poll(() => refusedReloads).toBeGreaterThanOrEqual(2);
    await expect(reload).toBeEnabled();
    mercariAnswers = true;
    const refreshed = page.waitForResponse(response => response.url().endsWith(`/native-marketplaces/mercari/form/refresh`));
    await reload.click();
    expect((await refreshed).ok()).toBe(true);
    await expect(reload).toHaveCount(0);
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
      await expect(page.getByRole("group", { name: `${market} listing fields` })).toBeVisible();
      await expect(page.getByRole("button", { name: "Reload fields", exact: true })).toHaveCount(0);
    }
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("stale marketplace fields reload automatically without clicking Reload fields", async ({ page, request, phone }, info) => {
  test.setTimeout(90_000);
  const title = `Auto reload fields ${info.project.name} ${Date.now()}`;
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

    // Without clicking Reload fields, the stale Mercari contract refreshes automatically.
    await expect.poll(async () => {
      const body = await (await request.get(`/api/conversations/${id}/listing`)).json();
      return body.listing._native_forms?.mercari?.inputs?.["direct_category_ids.mercari"];
    }).toBe("2008");
    const after = await (await request.get(`/api/conversations/${id}/native-marketplaces/preflight`)).json();
    expect(after.marketplaces.mercari.gaps.some((gap: { message: string }) => gap.message.includes("Reload its fields"))).toBe(false);
    await expect(form.getByText("The form's category or dependent choices changed.", { exact: false })).toHaveCount(0);
    await expect(form.getByRole("textbox", { name: "Title", exact: true })).toHaveValue("Seller's Mercari title");
    await expect(form.getByRole("button", { name: "Reload fields", exact: true })).toHaveCount(0);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("reload failures stay with their marketplace and revision", async ({ page, request, phone }, info) => {
  const title = `Scoped reload failures ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  let finishManual = () => {};
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Seller's description", price: 25,
      direct_category_ids: { mercari: "2008" },
      mercari_specifics: { title: "Original Mercari title", shippingMethod: "SOYO" },
      _native_forms: { mercari: { category_id: "old", page_labels: true,
        inputs: { "direct_category_ids.mercari": "old" },
        fields: [{ path: "direct_category_ids.mercari", label: "Category" }] } },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    await page.route(`**/api/conversations/${id}/native-marketplaces/preflight**`, async route => {
      const response = await route.fetch();
      const body = await response.json();
      body.marketplaces.ebay.gaps.push({ field: "direct_category_ids.ebay", message: "Reload its fields", blocking: true });
      await route.fulfill({ response, json: body });
    });
    let mercariCalls = 0;
    await page.route(`**/api/conversations/${id}/native-marketplaces/*/form/refresh`, async route => {
      if (route.request().url().endsWith("/mercari/form/refresh")) {
        mercariCalls += 1;
        if (mercariCalls === 2) await new Promise<void>(resolve => { finishManual = resolve; });
        if (mercariCalls >= 3) return route.continue();
      }
      await route.fulfill({ status: 500, json: { detail: "Marketplace did not answer." } });
    });
    await openListing(page, phone, title);
    await openForm(page, phone, "Mercari");
    const retry = page.getByRole("button", { name: "Reload fields", exact: true });
    await expect(retry).toBeEnabled();
    await openForm(page, phone, "eBay");
    await expect(retry).toBeEnabled();
    await openForm(page, phone, "Mercari");
    await expect(retry).toBeEnabled();
    await retry.click();
    await expect.poll(() => mercariCalls).toBe(2);
    await openForm(page, phone, "Etsy");
    await expect(page.getByRole("button", { name: "Reloading fields…" })).toHaveCount(0);
    await expect(page.getByText("Reloading fields…", { exact: true })).toHaveCount(0);
    await finishManual();
    await openForm(page, phone, "Mercari");
    await expect(retry).toBeEnabled();
    const titleInput = page.getByRole("group", { name: "Mercari listing fields" }).getByRole("textbox", { name: "Title", exact: true });
    await titleInput.fill("Edited Mercari title");
    await titleInput.blur();
    await expect.poll(async () => {
      const body = await (await request.get(`/api/conversations/${id}/listing`)).json();
      return body.listing._native_forms?.mercari?.inputs?.["direct_category_ids.mercari"];
    }).toBe("2008");
    await expect(retry).toHaveCount(0);
  } finally {
    finishManual();
    await request.delete(`/api/conversations/${id}`);
  }
});

test("a partial Mercari form offers a retry until shipping choices recover", async ({ page, request, phone }, info) => {
  const title = `Retry shipping quotes ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Seller's description", price: 25,
      direct_category_ids: { mercari: "2008" },
      mercari_specifics: { title, shippingMethod: "SOYO" },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    let quoteFailure = true;
    await page.route(`**/api/conversations/${id}/native-marketplaces/mercari/form?*`, async route => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({ response, json: quoteFailure ? {
        ...body, reload_recommended: true,
        notes: [...body.notes, "Mercari shipping labels could not be loaded. Reload choices to try again."],
      } : body });
    });
    await openListing(page, phone, title);
    await openForm(page, phone, "Mercari");
    const retry = page.getByRole("button", { name: "Reload fields", exact: true });
    await expect(retry).toBeEnabled();
    const response = page.waitForResponse(res => res.url().endsWith("/mercari/form/refresh"));
    quoteFailure = false;
    await retry.click();
    expect((await response).ok()).toBe(true);
    await expect(retry).toHaveCount(0);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

for (const recovery of ["manual retry", "returning to the tab"]) {
  test(`a saved reload recovers from a failed form read by ${recovery}`, async ({ page, request, phone }, info) => {
    const title = `Retry the refreshed form ${info.project.name} ${Date.now()}`;
    const id = await createListing(request, title);
    let releaseRead!: () => void;
    const readBlocked = new Promise<void>(resolve => { releaseRead = resolve; });
    try {
      const listingUrl = `/api/conversations/${id}/listing`;
      const saved = await request.put(listingUrl, { data: { expected_revision_id: "", listing: {
        title, description: "Seller's description", price: 25,
        direct_category_ids: { mercari: "2008" },
        mercari_specifics: { title, shippingMethod: "SOYO" },
        _native_forms: { mercari: { category_id: "old", page_labels: true,
          inputs: { "direct_category_ids.mercari": "old" },
          fields: [{ path: "direct_category_ids.mercari", label: "Category" }] } },
      } } });
      expect(saved.ok(), await saved.text()).toBe(true);
      const originalRevision = (await (await request.get(listingUrl)).json()).current_revision_id;
      const formUrl = `**/api/conversations/${id}/native-marketplaces/mercari/form`;
      let failRefresh = true;
      let failRead = true;
      let refreshedRevision = "";
      let lastRefreshInput = "";
      let preflightReads = 0;
      await page.route(`${formUrl}/refresh`, async route => {
        lastRefreshInput = route.request().postDataJSON().revision_id;
        if (failRefresh) {
          await route.fulfill({ status: 500, json: { detail: "Mercari did not answer." } });
          return;
        }
        const response = await route.fetch();
        refreshedRevision = (await response.json()).revision_id;
        await route.fulfill({ response });
      });
      await page.route(`${formUrl}?*`, async route => {
        if (failRead && refreshedRevision && new URL(route.request().url()).searchParams.get("revision_id") === refreshedRevision) {
          await readBlocked;
          await route.fulfill({ status: 500, json: { detail: "Could not read the refreshed choices." } });
        } else {
          await route.continue();
        }
      });
      await page.route(`**/api/conversations/${id}/native-marketplaces/preflight`, async route => {
        const response = await route.fetch();
        if (refreshedRevision && (await response.json()).revision_id === refreshedRevision) preflightReads += 1;
        await route.fulfill({ response });
      });

      await openListing(page, phone, title);
      await openForm(page, phone, "Mercari");
      const form = page.getByRole("group", { name: "Mercari listing fields" });
      const retry = form.getByRole("button", { name: "Reload fields", exact: true });
      await expect(retry).toBeEnabled();
      failRefresh = false;
      await retry.click();
      await expect.poll(() => preflightReads).toBe(1);
      expect(refreshedRevision).not.toBe(originalRevision);
      releaseRead();
      const status = form.getByRole("status").filter({ hasText: "Fields were saved, but the refreshed form could not be loaded." });
      await expect(status).toBeVisible();
      await expect(retry).toBeEnabled();
      await expect.poll(() => preflightReads).toBeGreaterThan(1);
      const retryRevision = refreshedRevision;
      failRead = false;
      if (recovery === "manual retry") {
        await retry.click();
      } else {
        await openForm(page, phone, "Etsy");
        const read = page.waitForResponse(res => res.url().includes("/mercari/form?")
          && new URL(res.url()).searchParams.get("revision_id") === retryRevision && res.ok());
        await openForm(page, phone, "Mercari");
        await read;
      }
      await expect(form.getByRole("textbox", { name: "Title", exact: true })).toHaveValue(title);
      await expect(status).toHaveCount(0);
      await expect(form.getByRole("button", { name: /^Reload(?:ing)? fields/ })).toHaveCount(0);
      expect(lastRefreshInput).toBe(recovery === "manual retry" ? retryRevision : originalRevision);
    } finally {
      releaseRead();
      await request.delete(`/api/conversations/${id}`);
    }
  });
}
