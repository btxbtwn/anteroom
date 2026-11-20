import { createListing, expect, openListing, test, titleBox } from "./fixtures";

test.beforeEach(async ({ page }) => {
  // The offline sandbox blocks the Mac updater; keep its unrelated polls out of these settings checks.
  await page.route(/\/api\/updates(?:\/progress)?$/, route => route.fulfill({ json: { available: false, status: "idle" } }));
  // Chrome pairing is not exercised here. A real status poll crossing the
  // reload can make WebKit report an access-control error for the old document.
  await page.route("**/api/extension/status", route => route.fulfill({ json: { connected: false, paired: false } }));
});

test("Vendoo is opt-in, replaces direct actions, and keeps manually edited drafts", async ({page, request, phone}, info) => {
  test.setTimeout(90_000);
  const title = `Vendoo mode ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  let sends = 0;
  let publications = 0;
  await page.route("**/api/conversations/*/native-marketplaces/*/list", async route => {
    publications++;
    await route.fulfill({status: 500, json: {detail: "No publication in this test"}});
  });
  await page.route(`**/api/conversations/${id}/vendoo-draft`, async route => {
    if (route.request().method() === "POST") sends++;
    await route.fulfill({json: {job_id: sends ? "qa-draft" : null, status: sends ? "completed" : null,
      step: sends ? "vendoo_saved" : null, error: null, revision_id: null,
      url: sends ? "https://web.vendoo.co/app/item/aaaaaaaaaaaaaaaaaaaa" : null}});
  });
  const settings = async () => {
    // Use the app navigation: reloading here can race newly mounted job reads
    // and WebKit rejects those reads while their document is being replaced.
    if (phone) {
      await page.getByRole("button", {name: "Inventory", exact: true}).click();
      await page.getByRole("button", {name: "Open navigation"}).click();
    }
    await page.getByRole("button", {name: "Settings", exact: true}).click();
    await page.getByRole("navigation", {name: "Settings sections"}).getByRole("button", {name: "Experimental", exact: true}).click();
    if (phone) {
      const close = page.getByRole("button", {name: "Close navigation"});
      if (await close.isVisible()) await close.click();
    }
  };
  try {
    await request.put("/api/settings/ui", {data: {listing_mode: "direct"}});
    await openListing(page, phone, title);
    await expect(page.getByRole("button", {name: "Send to Vendoo", exact: true})).toHaveCount(0);
    await settings();
    const toggle = page.getByRole("switch", {name: "Use Vendoo"});
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect.poll(async () => (await (await request.get("/api/settings/ui")).json()).listing_mode).toBe("vendoo");
    await openListing(page, phone, title);
    const input = await titleBox(page, phone);
    const saved = page.waitForResponse(response => response.url().endsWith(`/api/conversations/${id}/listing`)
      && response.request().method() === "PUT");
    await input.fill("Manually edited Vendoo draft");
    await input.blur();
    expect((await saved).ok()).toBe(true);
    await expect.poll(async () => (await (await request.get(`/api/conversations/${id}/listing`)).json()).listing.title).toBe("Manually edited Vendoo draft");
    await expect(page.getByRole("button", {name: /^List( on | everywhere|$)/})).toHaveCount(0);
    await page.getByRole("button", {name: "Send to Vendoo", exact: true}).click();
    await expect(page.getByRole("link", {name: "Review in Vendoo"})).toHaveAttribute("href", /web\.vendoo\.co\/app\/item\//);
    expect(sends).toBe(1);
    await page.reload();
    await openListing(page, phone, "Manually edited Vendoo draft");
    await titleBox(page, phone);
    await expect(page.getByRole("button", {name: "Save changes to Vendoo"})).toBeVisible();
    await settings();
    await page.getByRole("switch", {name: "Use Vendoo"}).click();
    await expect.poll(async () => (await (await request.get("/api/settings/ui")).json()).listing_mode).toBe("direct");
    await openListing(page, phone, "Manually edited Vendoo draft");
    await expect(page.getByRole("button", {name: /Send to Vendoo|Save changes to Vendoo/})).toHaveCount(0);
    expect(publications).toBe(0);
  } finally {
    await page.goto("about:blank");
    await request.put("/api/settings/ui", {data: {listing_mode: "direct"}});
    await request.delete(`/api/conversations/${id}`);
  }
});
