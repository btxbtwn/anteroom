import { createListing, expect, openListing, test } from "./fixtures";

// The form gets the room by default: Ant waits as a launcher on a listing with
// no chat, the listing's actions share one row, and scheduling shows only on a draft.

async function savedCopy(request: Parameters<typeof createListing>[0], title: string): Promise<string> {
  const id = await createListing(request, title);
  const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
    title, description: "Black high rise skinny jeans.", price: 40, quantity: 1,
  } } });
  expect(saved.ok(), await saved.text()).toBe(true);
  return id;
}

test("Ant starts as a launcher on a listing with no chat and remembers being minimized", async ({ page, phone, request }, info) => {
  test.skip(phone, "On a phone Ant is its own tab.");
  test.setTimeout(120_000);
  const title = `Editor room ${info.project.name} ${Date.now()}`;
  const draft = `Editor room draft ${info.project.name} ${Date.now()}`;
  const id = await savedCopy(request, title);
  const draftId = await createListing(request, draft);
  try {
    const chat = page.getByRole("region", { name: "Listing chat" });
    const launcher = page.locator("main").getByRole("button", { name: /^Ask Ant/ });
    const form = page.getByRole("group", { name: "General listing fields" });
    for (const width of [1440, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await openListing(page, phone, title);
      await expect(launcher).toBeVisible({ timeout: 30_000 });
      await expect(chat).toBeHidden();
      // Without Ant's column the form is the widest part of the page.
      const box = await form.boundingBox();
      expect(box?.width ?? 0).toBeGreaterThan(width === 1440 ? 640 : 520);
      await page.screenshot({ path: info.outputPath(`editor-${width}.png`) });
    }

    // Opening is instant and stays on this listing.
    await launcher.click();
    await expect(chat).toBeVisible();
    await chat.getByRole("button", { name: "Minimize chat" }).click();
    await expect(chat).toBeHidden();

    // A draft waiting for Generate would open Ant, but the seller minimized it.
    await openListing(page, phone, draft);
    await expect(page.getByRole("navigation", { name: "Marketplaces" })).toBeVisible({ timeout: 30_000 });
    await expect(launcher).toBeVisible();
    await expect(chat).toBeHidden();
    await page.keyboard.press(process.platform === "darwin" ? "Meta+J" : "Control+J");
    await expect(chat).toBeVisible();
  } finally {
    await request.delete(`/api/conversations/${id}`);
    await request.delete(`/api/conversations/${draftId}`);
  }
});

test("the listing's actions share one row, scheduling opens from them, and a sale is recorded one way", async ({ page, phone, request }, info) => {
  test.setTimeout(120_000);
  const title = `Editor actions ${info.project.name} ${Date.now()}`;
  const id = await savedCopy(request, title);
  try {
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    const rail = page.getByRole("navigation", { name: "Marketplaces" });
    await expect(rail).toBeVisible({ timeout: 30_000 });
    // Scheduling is no longer a panel above the photos.
    await expect(page.getByText("Ready to list & schedule publish")).toHaveCount(0);

    const more = page.getByRole("button", { name: "More listing actions" });
    if (phone) {
      await more.click();
      await page.getByRole("menuitem", { name: "Schedule…" }).click();
    } else {
      const toolbar = page.getByRole("toolbar", { name: "Listing actions" });
      await expect(toolbar.getByRole("button", { name: "Regenerate…" })).toBeVisible();
      await page.screenshot({ path: info.outputPath("listing-actions.png") });
      await toolbar.getByRole("button", { name: "Schedule…" }).click();
    }
    const dialog = page.getByRole("dialog", { name: "Schedule publishing" });
    await expect(dialog.getByRole("switch", { name: "Mark draft ready to list" })).toBeVisible();
    await page.screenshot({ path: info.outputPath("schedule-dialog.png") });
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();

    // The listing's menu: Mark as sold opens the sale form; Record a sale is gone.
    await more.click();
    await expect(page.getByRole("menuitem", { name: /Record a sale/ })).toHaveCount(0);
    await page.getByRole("menuitem", { name: "Mark as sold…" }).click();
    const sale = page.getByRole("form", { name: "Mark as sold" });
    await expect(sale.getByRole("combobox", { name: "Where it sold" })).toBeVisible();
    await sale.getByRole("button", { name: "Cancel" }).click();
    await expect(sale).toBeHidden();

    // A marketplace's menu keeps its bookkeeping under Tracking.
    if (phone) {
      await rail.getByRole("button", { name: /^Form, / }).click();
      await page.getByRole("menuitem", { name: /^Mercari/ }).click();
      await page.getByRole("button", { name: "Mercari actions" }).click();
    } else {
      await rail.getByRole("listitem", { name: "Mercari" }).hover();
      await rail.getByRole("button", { name: "Mercari actions" }).click();
    }
    const tracking = page.getByRole("group", { name: "Tracking" });
    await expect(tracking.getByRole("menuitem")).toHaveText(["Mark as listed…", "Mark as sold…"]);
    await page.screenshot({ path: info.outputPath("marketplace-menu.png") });
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
