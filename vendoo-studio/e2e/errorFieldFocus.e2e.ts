import { createListing, expect, openListing, openForm, test } from "./fixtures";

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
