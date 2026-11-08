import { createListing, expect, openListing, test } from "./fixtures";

// A listing that already has copy invites questions and edits; Generate would
// overwrite it. A draft that only has photos still leads with Generate.

test("a listing with saved copy opens on the listing and its chat invites edits", async ({ page, phone, request }, info) => {
  test.setTimeout(120_000);
  const title = `Saved copy ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Black high rise skinny jeans.", quantity: 1,
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    await openListing(page, phone, title);

    if (phone) {
      // An existing listing opens on its form, not on chat.
      await expect(page.getByRole("tab", { name: "Listing" })).toHaveAttribute("aria-selected", "true", { timeout: 30_000 });
      await info.attach("existing-listing-opens-on-form.png", { body: await page.screenshot(), contentType: "image/png" });
      await page.getByRole("tab", { name: "Chat" }).click();
    }
    const chat = phone ? page.locator("main") : page.getByRole("region", { name: "Listing chat" });
    await expect(chat.getByText("Ask about this listing")).toBeVisible({ timeout: 30_000 });
    await info.attach("existing-listing-chat.png", { body: await page.screenshot(), contentType: "image/png" });
    await expect(chat.getByRole("button", { name: "Generate listing" })).toHaveCount(0);

    // Regenerate is offered, but quietly, and asks before it rewrites anything.
    const regenerate = chat.getByRole("button", { name: "Regenerate…", exact: true });
    await expect(regenerate).toBeVisible();
    await regenerate.click();
    const confirm = page.getByRole("alertdialog").or(page.getByRole("dialog"));
    await expect(confirm).toContainText("Rewrite this listing from scratch?");
    await confirm.getByRole("button", { name: "Cancel" }).click();
    await expect(confirm).toBeHidden();
    const listing = await (await request.get(`/api/conversations/${id}/listing`)).json();
    expect(listing.listing.description).toBe("Black high rise skinny jeans.");
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("a draft with only photos opens on chat with Generate first", async ({ page, phone, request }, info) => {
  test.setTimeout(120_000);
  const title = `Photos only ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await openListing(page, phone, title);
    if (phone) await expect(page.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true", { timeout: 30_000 });
    await expect(page.getByRole("button", { name: "Generate listing", exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Ask about this listing")).toHaveCount(0);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
