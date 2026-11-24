import { createListing, expect, openListing, openForm, test } from "./fixtures";

test("the pinned validation summary takes a delisted Depop draft to Size", async ({ page, phone, request }, info) => {
  await page.emulateMedia({ colorScheme: phone ? "dark" : "light" });
  const title = `Depop size to fix ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Black cotton T-shirt", price: 25, quantity: 1,
      direct_category_ids: { depop: "menswear_tshirts" },
      depop_specifics: { size: "", parcelSize: "", condition: "used_good" },
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    await page.route(`**/api/conversations/${id}/marketplace-listings`, route => route.fulfill({ json: {
      depop: { listing_id: "previous", state: "deleted", sold_here: false },
    } }));
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing", exact: true }).click();
    await openForm(page, phone, "Depop");

    const summary = page.getByRole("region", { name: "Depop validation" });
    const disclosure = summary.getByRole("button", { name: /to review/ });
    const fix = summary.getByRole("button", { name: "Fix Size", exact: true });
    const fixWithAnt = summary.getByRole("button", { name: "Fix with Ant", exact: true });
    const message = "Choose a clothing size for this Depop category.";
    await expect(summary.getByText(message, { exact: true })).toBeInViewport();
    await expect(fixWithAnt).toBeEnabled();
    await expect(fix).toBeEnabled();

    if (phone) await fix.tap();
    else {
      await fix.focus();
      await fix.press("Enter");
    }
    const form = page.getByRole("group", { name: "Depop listing fields" });
    const size = form.getByRole("combobox", { name: /^Size\b/ });
    await expect(size).toBeFocused();
    await expect(size).toBeInViewport({ ratio: 1 });
    await expect(form.getByText(message, { exact: true })).toBeInViewport();
    await expect(fix).toBeInViewport();
    await page.screenshot({ path: info.outputPath("validation-summary.png") });
    await page.emulateMedia({ colorScheme: phone ? "light" : "dark" });
    await expect.poll(() => page.locator("html").evaluate((node) => node.classList.contains("dark"))).toBe(!phone);
    await page.screenshot({ path: info.outputPath("validation-summary-alternate-theme.png") });

    await disclosure.click();
    await expect(disclosure).toHaveAttribute("aria-expanded", "false");
    await size.fill("M");
    await page.getByRole("option", { name: "M", exact: true }).click({ timeout: 5_000 });
    await expect(form.getByText(message, { exact: true })).toHaveCount(0);
    await expect(disclosure).toHaveText("1 issue to review");
    await expect(disclosure).toHaveAttribute("aria-expanded", "false");
    await disclosure.click();
    await expect(summary.getByText(message, { exact: true })).toHaveCount(0);
    await expect(fix).toHaveCount(0);
    await expect(summary.getByRole("button", { name: "Fix Package size" })).toBeVisible();

    const [reply] = await Promise.all([
      page.waitForResponse((response) => response.url().endsWith(`/api/conversations/${id}/messages`)
        && response.request().method() === "POST"),
      fixWithAnt.click(),
    ]);
    expect(reply.ok()).toBe(true);
    expect(reply.request().postDataJSON().text).toContain("Depop needs a parcel size.");
    if (phone) await expect(page.getByRole("tab", { name: "Ant", exact: true })).toHaveAttribute("aria-selected", "true");
    else await expect(page.getByRole("region", { name: "Listing chat" })).toBeVisible();
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("Edit on a marketplace refusal focuses that field's input", async ({ page, phone, request }, info) => {
  const title = `Error field focus ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Private test", price: 25, quantity: 1,
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    // No marketplace request leaves the browser: the status is answered here.
    await page.route(`**/api/conversations/${id}/native-marketplaces**`, async route => {
      if (new URL(route.request().url()).pathname.endsWith("/native-marketplaces")) {
        await route.fulfill({ json: { poshmark: { state: "not_saved", running: false, error_code: "missing_fields",
          error: "Poshmark needs a better title.", error_fields: [{ field: "title", message: "Title is too short." }] } } });
      } else await route.continue();
    });
    await openListing(page, phone, title);
    await openForm(page, phone, "Poshmark");
    await page.getByRole("button", { name: "Edit Title", exact: true }).first().click();
    await expect(page.getByRole("textbox", { name: "Title *", exact: true })).toBeFocused();
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
