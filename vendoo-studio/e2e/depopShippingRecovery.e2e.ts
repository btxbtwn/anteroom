import { createListing, expect, openListing, openForm, test } from "./fixtures";

for (const tablet of [false, true]) {
  test(`persisted Depop shipping errors reach Package size repeatedly${tablet ? " at tablet landscape" : ""}`, async ({ page, phone, request }, info) => {
    test.setTimeout(90_000);
    test.skip(tablet && info.project.name !== "phone-webkit", "Tablet coverage uses WebKit");
    if (tablet) await page.setViewportSize({ width: 1180, height: 820 });
    const mobile = phone && !tablet;
    const title = `Shipping recovery ${info.project.name} ${tablet} ${Date.now()}`;
    const id = await createListing(request, title);
    try {
      const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
        title, description: "Sandbox shipping fixture", price: 25, quantity: 1,
        depop_specifics: { parcelSize: "Large" },
      } } });
      expect(saved.ok(), await saved.text()).toBe(true);
      await page.route(`**/api/conversations/${id}/native-marketplaces`, route => route.fulfill({ json: {
        depop: { state: "draft", running: false, error_code: "missing_fields", error: "Depop needs shipping.",
          marketplace_message: "missing_fields: national_shipping", error_fields: [
            { field: "national_shipping", message: "Depop needs shipping." },
            { field: "shipping_methods", message: "Depop needs shipping." },
          ] },
      } }));
      await openListing(page, mobile, title);
      if (mobile) await page.getByRole("tab", { name: "Listing", exact: true }).click();
      await openForm(page, mobile, "Depop");
      const recovery = page.getByRole("button", { name: "Edit Package size", exact: true });
      await expect(recovery).toHaveCount(1);
      await expect(page.getByText(/Check Package size here/)).toBeVisible();
      await expect(page.getByRole("button", { name: "Edit National shipping" })).toHaveCount(0);
      const size = page.getByRole("combobox", { name: /^Package size/ });
      for (let attempt = 0; attempt < 2; attempt += 1) {
        await expect(size).toBeVisible({ timeout: 20_000 });
        await recovery.click();
        await expect(size).toBeFocused({ timeout: 15_000 });
        await expect(size).toBeEnabled();
        const value = attempt ? "Large" : "Medium";
        if (await size.evaluate(element => element.tagName === "SELECT")) await size.selectOption(value);
        else {
          await size.fill(value);
          await page.getByRole("option", { name: value, exact: true }).click();
        }
        await expect.poll(async () => (await (await request.get(`/api/conversations/${id}/listing`)).json())
          .listing.depop_specifics.parcelSize).toBe(attempt ? "Large" : "Medium");
        if (mobile) await openForm(page, mobile, "General");
        else await page.getByRole("navigation", { name: "Marketplaces" }).getByRole("button", { name: "General", exact: true }).click();
        await openForm(page, mobile, "Depop");
      }
      // Editing a package never clears a still-unresolved marketplace refusal.
      await expect(page.getByText("Depop needs shipping.", { exact: true }).first()).toBeVisible();
      await page.screenshot({ path: info.outputPath("depop-shipping-recovery.png"), fullPage: true });
      await recovery.scrollIntoViewIfNeeded();
      await page.screenshot({ path: info.outputPath("depop-shipping-guidance.png"), fullPage: true });
    } finally {
      await request.delete(`/api/conversations/${id}`);
    }
  });
}
