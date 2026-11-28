import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createListing, expect, openListing, showStatusTab, test } from "./fixtures";

test("bulk sizes are read from tags, checked by the seller and saved with their own drafts", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const title = `Bulk size ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const directory = await mkdtemp(join(tmpdir(), "bulk-size-"));
  const created: string[] = [];
  const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64");
  // The sandbox has no photo model, so each tag reading is stubbed by photo name; the rest is the real app.
  const tags: Record<string, object> = {
    "shorts.png": { photos_read: 1, status: "accepted", size: "0", sizes: ["0"] },
    "pants.png": { photos_read: 1, status: "conflict", size: null, sizes: ["32 x 30", "34 x 30"] },
    "shirt.png": { photos_read: 1, status: "accepted", size: "M", sizes: ["M"] },
  };
  await page.route("**/api/size-tags/read", async (route) => {
    const name = Object.keys(tags).find((file) => route.request().postDataBuffer()?.includes(file));
    await route.fulfill({ json: tags[name!] });
  });
  try {
    for (const name of ["Shirt", "Pants", "Shorts"]) {
      await mkdir(join(directory, name));
      await writeFile(join(directory, name, `${name.toLowerCase()}.png`), pixel);
    }
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    await page.locator("input[webkitdirectory]").setInputFiles(directory);
    const dialog = page.getByRole("dialog", { name: "Set details for 3 listings" });
    const size = (item: string) => dialog.getByRole("textbox", { name: `Size for ${item}`, exact: true });
    // A size the seller typed is kept, whatever the tag reads.
    await size("Shirt").fill("L");
    await dialog.getByRole("textbox", { name: "SKU for Shirt", exact: true }).fill("SHIRT-1");

    await dialog.getByRole("button", { name: "Read size tags" }).click();
    await expect(size("Shorts")).toHaveValue("0");
    await expect(size("Shirt")).toHaveValue("L");
    await expect(dialog.getByText("The tag reads M. Check the size you typed.")).toBeVisible();
    await expect(size("Pants")).toHaveValue("");
    await expect(dialog.getByText("The photos show different sizes (32 x 30, 34 x 30). Check the tag and type the size.")).toBeVisible();
    await expect(dialog).not.toContainText(/openrouter|gemini|google/i);
    await size("Pants").scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`bulk-size-tags-${info.project.name}.png`) });

    await size("Pants").fill("32 x 30");
    page.on("response", async response => {
      if (response.request().method() === "POST" && response.url().endsWith("/api/conversations") && response.ok()) {
        created.push((await response.json()).id);
      }
    });
    await dialog.getByRole("button", { name: "Create drafts only" }).click();
    await expect(dialog).toBeHidden();
    await expect.poll(() => created.length, { timeout: 30_000 }).toBe(3);
    const drafts = await Promise.all(created.map(async draftID => (await request.get(`/api/conversations/${draftID}`)).json()));
    const notes = Object.fromEntries(drafts.map(draft => [draft.title, JSON.parse(draft.notes)]));
    // Structured Size, not a sentence in the seller's notes; the package size is untouched.
    expect(Object.fromEntries(Object.entries(notes).map(([item, note]) => [item, note.size]))).toEqual({ Shirt: "L", Pants: "32 x 30", Shorts: "0" });
    expect(notes.Shirt.sku).toBe("SHIRT-1");
    for (const note of Object.values(notes)) {
      expect(note.sellerNotes).toBeUndefined();
      expect(note.packageDimensions).toBeUndefined();
    }
  } finally {
    for (const draftID of [id, ...created]) await request.delete(`/api/conversations/${draftID}`);
    await rm(directory, { recursive: true, force: true });
  }
});

test("bulk upload and regenerate review dialogs trap focus and close on Escape", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const title = `Bulk dialog ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const directory = await mkdtemp(join(tmpdir(), "bulk-dialog-"));
  const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64");
  try {
    await mkdir(join(directory, "Jacket"));
    await writeFile(join(directory, "Jacket", "front.png"), pixel);

    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    await page.locator("input[webkitdirectory]").setInputFiles(directory);

    const bulkDialog = page.getByRole("dialog", { name: "Set details for 1 listings" });
    await expect(bulkDialog).toBeVisible();
    await expect(bulkDialog.getByLabel("Cost of goods", { exact: true })).toBeVisible();
    await expect(bulkDialog).not.toContainText("Cost of goods (COG)");

    // Tabbing forward and backward keeps focus trapped inside BulkUploadDialog.
    for (let step = 0; step < 16; step += 1) {
      await page.keyboard.press("Tab");
      await expect.poll(() => bulkDialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    }
    await page.keyboard.press("Shift+Tab");
    await expect.poll(() => bulkDialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);

    // Pressing Escape closes BulkUploadDialog.
    await page.keyboard.press("Escape");
    await expect(bulkDialog).toBeHidden();

    // Now open RegenerateReviewDialog via Select listings → More → Regenerate….
    if (phone) {
      await page.goto("/");
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    }
    await showStatusTab(page, "Draft");
    await page.getByRole("searchbox", { name: /^Search listings/ }).fill(title);
    await page.getByRole("button", { name: "Select listings", exact: true }).click();
    await page.getByRole("list", { name: "Listings" }).getByRole("checkbox", { name: `Select ${title}`, exact: true }).click();
    const bar = page.getByRole("region", { name: "Bulk listing actions" });
    await bar.getByRole("button", { name: "More", exact: true }).click();
    await page.getByRole("menuitem", { name: "Regenerate…", exact: true }).click();

    const regenDialog = page.getByRole("dialog", { name: "Review 1 listing before rewriting" });
    await expect(regenDialog).toBeVisible();
    await expect(regenDialog.getByLabel("Cost of goods", { exact: true })).toBeVisible();
    await expect(regenDialog).not.toContainText("Cost of goods (COG)");

    for (let step = 0; step < 16; step += 1) {
      await page.keyboard.press("Tab");
      await expect.poll(() => regenDialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    }
    await page.keyboard.press("Shift+Tab");
    await expect.poll(() => regenDialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(regenDialog).toBeHidden();
  } finally {
    await request.delete(`/api/conversations/${id}`);
    await rm(directory, { recursive: true, force: true });
  }
});
