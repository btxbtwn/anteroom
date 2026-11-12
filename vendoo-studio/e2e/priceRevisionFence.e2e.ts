import { createListing, expect, openListing, test } from "./fixtures";

test("a reviewed markdown cannot overwrite a newer price", async ({ page, phone, request }, info) => {
  test.setTimeout(120_000);
  const title = `Reviewed markdown ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: {
      expected_revision_id: "", listing: { title, description: "Black jeans.", price: 40, quantity: 1 },
    } });
    expect(saved.ok(), await saved.text()).toBe(true);
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Chat" }).click();
    await page.getByRole("button", { name: "Regenerate…", exact: true }).click();
    const dialog = page.getByRole("dialog").or(page.getByRole("alertdialog"));
    const confirm = dialog.getByRole("button", { name: "Confirm", exact: true });
    await expect(confirm).toBeEnabled({ timeout: 30_000 });
    const before = await (await request.get(`/api/conversations/${id}/listing`)).json();
    const edited = await request.put(`/api/conversations/${id}/listing`, { data: {
      expected_revision_id: before.current_revision_id, listing: { ...before.listing, price: 60 },
    } });
    expect(edited.ok(), await edited.text()).toBe(true);
    const response = page.waitForResponse(r => r.url().endsWith(`/api/conversations/${id}/price-drop`) && r.request().method() === "POST");
    await confirm.click();
    expect((await response).status()).toBe(409);
    await expect(dialog).toBeVisible();
    const after = await (await request.get(`/api/conversations/${id}/listing`)).json();
    expect(after.listing.price).toBe(60);
    await info.attach("stale-markdown-keeps-new-price.png", { body: await page.screenshot(), contentType: "image/png" });
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
