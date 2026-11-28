import { createListing, expect, openForm, openListing, test } from "./fixtures";

test("View opens the saved marketplace link when the result is unconfirmed", async ({ context, page, phone, request }, info) => {
  const title = `Unconfirmed delist ${info.project.name} ${Date.now()}`;
  const url = "https://www.depop.com/products/sandbox-original-tee/";
  const id = await createListing(request, title);
  try {
    const marked = await request.post(`/api/conversations/${id}/marketplace-listings/depop/mark`, {
      data: { state: "listed", url },
    });
    expect(marked.ok(), await marked.text()).toBe(true);
    // Simulate a missing confirmation without sending a delist to Depop.
    let verified = false;
    await page.route(`**/api/conversations/${id}/native-marketplaces`, (route) => route.fulfill({
      json: { depop: verified ? { state: "published", running: false, listing_url: url }
        : { state: "unknown", running: false, verification: "checking" } },
    }));
    await context.route(url, (route) => route.fulfill({ contentType: "text/html", body: "<h1>Original listing</h1>" }));

    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing", exact: true }).click();
    await openForm(page, phone, "Depop");
    const pane = page.locator('[data-marketplace-pane="depop"]');
    await expect(pane.getByText("Checking Depop…", { exact: true })).toBeVisible();
    await expect(pane.getByRole("button", { name: "Check result" })).toHaveCount(0);
    const view = pane.getByLabel("View on Depop");
    await expect(view).toHaveAttribute("href", url);
    await page.screenshot({ path: info.outputPath("unconfirmed-delist.png") });
    if (phone) {
      await page.setViewportSize({ width: 320, height: 640 });
      await expect.poll(() => pane.evaluate(element => element.scrollWidth - element.clientWidth)).toBe(0);
    }
    const [listing] = await Promise.all([page.waitForEvent("popup"), view.click()]);
    await expect(listing.getByRole("heading", { name: "Original listing" })).toBeVisible();
    await listing.close();
    verified = true;
    await expect(pane.getByLabel("View listing on Depop")).toHaveAttribute("href", url, { timeout: 15000 });
    await expect(pane.getByText("Checking Depop…", { exact: true })).toHaveCount(0);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
