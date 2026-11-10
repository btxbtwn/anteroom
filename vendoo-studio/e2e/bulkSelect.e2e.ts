import { createListing, expect, test } from "./fixtures";

test("Select mode keeps every action off until a listing is picked, and only Delete is destructive", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const title = `Bulk select ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await page.goto("/");
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    }
    await page.getByRole("button", { name: "Select listings", exact: true }).click();
    const bar = page.getByRole("region", { name: "Bulk listing actions" });
    await expect(bar.getByRole("status")).toHaveText("0 selected");
    for (const name of ["Marketplaces", "Edit fields", "Mark down", "Copy items", "More"]) {
      await expect(bar.getByRole("button", { name, exact: true })).toBeDisabled();
    }
    await info.attach("bulk-none-selected.png", { body: await page.screenshot(), contentType: "image/png" });

    await page.getByRole("list", { name: "Listings" }).getByRole("checkbox", { name: `Select ${title}`, exact: true }).click();
    await expect(bar.getByRole("status")).toHaveText("1 selected");
    await bar.getByRole("button", { name: "More", exact: true }).click();
    const remove = page.getByRole("menuitem", { name: "Delete…", exact: true });
    await expect(remove).toBeVisible();
    // Delete is the menu's destructive item; Regenerate is an ordinary one.
    await expect(remove).toHaveAttribute("data-variant", "destructive");
    await expect(page.getByRole("menuitem", { name: "Regenerate…", exact: true })).not.toHaveAttribute("data-variant", "destructive");
    await info.attach("bulk-more-menu.png", { body: await page.screenshot(), contentType: "image/png" });
    await page.keyboard.press("Escape");
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
