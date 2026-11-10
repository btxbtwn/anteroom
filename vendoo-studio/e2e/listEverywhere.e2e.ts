import { createListing, expect, openListing, test } from "./fixtures";

test("List everywhere lists only the marketplaces the seller picks, after they confirm", async ({ page, phone, request }, info) => {
  const title = `Marketplace picker ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Private test", price: 25, quantity: 1,
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    const revision = (await saved.json()).current_revision_id;
    const listed: { marketplace: string; confirm: unknown }[] = [];
    // No marketplace mutation leaves the browser. The sandbox also refuses real publication.
    await page.route(`**/api/conversations/${id}/native-marketplaces**`, async route => {
      const path = new URL(route.request().url()).pathname;
      const list = path.match(/\/native-marketplaces\/(\w+)\/list$/);
      if (path.endsWith("/preflight")) {
        await route.fulfill({ json: { revision_id: revision, marketplaces: {
          ebay: { ready: true, gaps: [], missing: [] },
          poshmark: { ready: true, gaps: [], missing: [] },
          depop: { ready: false, gaps: [{ field: "depop_specifics.size", message: "Depop needs a size.", blocking: true }],
            missing: [{ field: "depop_specifics.size", label: "Size", message: "Depop needs a size." }] },
        } } });
      } else if (list) {
        listed.push({ marketplace: list[1], confirm: route.request().postDataJSON().confirm });
        await route.fulfill({ json: { state: "draft", running: false } });
      } else if (path.endsWith("/native-marketplaces")) {
        await route.fulfill({ json: {} });
      } else await route.continue();
    });
    test.setTimeout(90_000);
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();

    const rail = page.getByRole("navigation", { name: "Marketplaces" });
    if (phone) {
      // A phone picks forms and listing-wide actions from the picker above the form.
      await rail.getByRole("button", { name: /^Form, / }).click();
      await expect(page.getByRole("menuitem", { name: /^eBay/ })).toContainText("Ready", { timeout: 20_000 });
      await expect(page.getByRole("menuitem", { name: /^Depop/ })).toContainText("1 to fill");
      await page.getByRole("menuitem", { name: "List everywhere…" }).click();
    } else {
      await expect(rail.getByRole("listitem", { name: "eBay" })).toContainText("Ready", { timeout: 20_000 });
      await expect(rail.getByRole("listitem", { name: "Depop" })).toContainText("1 to fill");
      await rail.getByRole("button", { name: "List everywhere…" }).click();
    }
    const dialog = page.getByRole("dialog");
    const confirm = dialog.getByRole("button", { name: /^List/ });
    // Nothing is picked until the seller picks it.
    await expect(dialog.getByRole("status")).toHaveText(/^0 of \d+ selected$/);
    await expect(confirm).toBeDisabled();
    await expect(dialog.getByRole("listitem").filter({ hasText: "Depop" })).toContainText("Needs 1 field");

    await dialog.getByRole("button", { name: "Select all ready" }).click();
    await expect(dialog.getByRole("checkbox", { name: "List on eBay" })).toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: "List on Poshmark" })).toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: "List on Depop" })).not.toBeChecked();
    await dialog.getByRole("checkbox", { name: "List on Poshmark" }).click();
    await expect(dialog.getByRole("status")).toHaveText(/^1 of \d+ selected$/);
    expect(listed).toEqual([]);

    await dialog.getByRole("button", { name: "List on eBay" }).click();
    await expect(dialog).toBeHidden();
    await expect.poll(() => listed).toEqual([{ marketplace: "ebay", confirm: true }]);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("List everywhere names each missing field in full and opens it on its form", async ({ page, phone, request }, info) => {
  const title = `Missing fields ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const question = "Will your item ship with a prepaid label from Mercari?";
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Private test", price: 25, quantity: 1,
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    const revision = (await saved.json()).current_revision_id;
    const listed: string[] = [];
    await page.route(`**/api/conversations/${id}/native-marketplaces**`, async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/preflight")) {
        await route.fulfill({ json: { revision_id: revision, marketplaces: {
          ebay: { ready: true, gaps: [], missing: [] },
          poshmark: { ready: true, gaps: [], missing: [] },
          mercari: { ready: false, gaps: [], missing: [
            { field: "mercari_specifics.shipsFrom", label: "Ships from", message: "Mercari needs where it ships from." },
            { field: "mercari_specifics.prepaid", label: question, message: "Required." },
          ] },
        } } });
      } else if (/\/list$/.test(path)) {
        listed.push(path);
        await route.fulfill({ json: { state: "draft", running: false } });
      } else if (path.endsWith("/native-marketplaces")) {
        await route.fulfill({ json: {} });
      } else await route.continue();
    });
    test.setTimeout(90_000);
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    const rail = page.getByRole("navigation", { name: "Marketplaces" });
    if (phone) {
      await rail.getByRole("button", { name: /^Form, / }).click();
      await expect(page.getByRole("menuitem", { name: /^Mercari/ })).toContainText("2 to fill", { timeout: 20_000 });
      await page.getByRole("menuitem", { name: "List everywhere…" }).click();
    } else {
      await expect(rail.getByRole("listitem", { name: "Mercari" })).toContainText("2 to fill", { timeout: 20_000 });
      await rail.getByRole("button", { name: "List everywhere…" }).click();
    }
    const dialog = page.getByRole("dialog");
    const needs = dialog.getByRole("list", { name: "Mercari needs" });
    // Full names, no stray punctuation, nothing cut off.
    await expect(needs.getByRole("button")).toHaveText(["Ships from", question]);
    const long = needs.getByRole("button", { name: question });
    expect(await long.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    await info.attach("list-everywhere-missing-fields.png", { body: await page.screenshot(), contentType: "image/png" });

    await needs.getByRole("button", { name: "Ships from" }).click();
    await expect(dialog).toBeHidden();
    if (phone) await expect(rail.getByRole("button", { name: "Form, Mercari" })).toBeVisible();
    else await expect(rail.getByRole("listitem", { name: "Mercari" }).getByRole("button").first()).toHaveAttribute("aria-current", "true");
    expect(listed).toEqual([]);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("the listing chat is a column beside a wide form and floats over a narrow one", async ({ page, phone, request }, info) => {
  test.skip(phone, "On a phone the chat is its own pane.");
  test.setTimeout(90_000);
  const title = `Editor layout ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await openListing(page, phone, title);
    const chat = page.getByRole("region", { name: "Listing chat" });
    await expect(chat).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("separator", { name: "Resize chat" })).toHaveCount(1);
    await expect(chat.getByRole("button", { name: "Make chat larger" })).toHaveCount(0);

    await page.setViewportSize({ width: 1100, height: 760 });
    await expect(page.getByRole("separator", { name: "Resize chat" })).toHaveCount(0);
    await chat.getByRole("button", { name: "Make chat larger" }).click();
    await expect(chat.getByRole("button", { name: "Make chat smaller" })).toBeVisible();
    await chat.getByRole("button", { name: "Make chat smaller" }).click();

    // Minimized, the chat is a pill and the form gets the whole width.
    await chat.getByRole("button", { name: "Minimize chat" }).click();
    await expect(chat).toBeHidden();
    await expect(page.locator("main").getByRole("button", { name: /^Chat/ })).toBeVisible();
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
