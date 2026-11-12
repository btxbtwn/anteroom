import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createListing, expect, openListing, test } from "./fixtures";

test("bulk size tags stay with their own saved drafts", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const title = `Bulk size ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const directory = await mkdtemp(join(tmpdir(), "bulk-size-"));
  const created: string[] = [];
  const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64");
  try {
    for (const name of ["Shirt", "Pants"]) {
      await mkdir(join(directory, name));
      await writeFile(join(directory, name, "front.png"), pixel);
    }
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    await page.locator("input[webkitdirectory]").setInputFiles(directory);
    const dialog = page.getByRole("dialog", { name: "Set details for 2 listings" });
    await dialog.getByRole("textbox", { name: "Size tag for Shirt", exact: true }).fill(" XL / TG ");
    await dialog.getByRole("textbox", { name: "Size tag for Pants", exact: true }).fill("32 × 30");
    await dialog.getByRole("textbox", { name: "SKU for Shirt", exact: true }).fill("SHIRT-1");
    await dialog.getByRole("textbox", { name: "Size tag for Shirt", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/anteroom-bulk-size-${info.project.name}.png` });
    page.on("response", async response => {
      if (response.request().method() === "POST" && response.url().endsWith("/api/conversations") && response.ok()) {
        created.push((await response.json()).id);
      }
    });
    await dialog.getByRole("button", { name: "Create drafts only" }).click();
    await expect(dialog).toBeHidden();
    await expect.poll(() => created.length, { timeout: 30_000 }).toBe(2);
    const drafts = await Promise.all(created.map(async draftID => (await request.get(`/api/conversations/${draftID}`)).json()));
    expect(drafts.map(draft => JSON.parse(draft.notes).sellerNotes).sort()).toEqual(["Size tag: 32 × 30", "Size tag: XL / TG"]);
    expect(JSON.parse(drafts.find(draft => draft.title === "Shirt").notes).sku).toBe("SHIRT-1");
  } finally {
    for (const draftID of [id, ...created]) await request.delete(`/api/conversations/${draftID}`);
    await rm(directory, { recursive: true, force: true });
  }
});
