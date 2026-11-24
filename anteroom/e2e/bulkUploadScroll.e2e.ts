import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createListing, expect, openListing, test } from "./fixtures";

test("a 25-item folder upload loads nearby previews and preserves edits while scrolling", async ({ page, phone, request }, info) => {
  const title = `Bulk scroll ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const directory = await mkdtemp(join(tmpdir(), "bulk-scroll-"));
  try {
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    // Camera-sized images make the cost of eager loading visible even with small fixture files.
    const jpeg = await page.evaluate(() => {
      const canvas = document.createElement("canvas");
      canvas.width = 4032;
      canvas.height = 3024;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#b78253";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#5e4734";
      ctx.fillRect(1000, 500, 1500, 2000);
      return canvas.toDataURL("image/jpeg").split(",")[1]!;
    });
    const photo = Buffer.from(jpeg, "base64");
    for (let item = 1; item <= 25; item += 1) {
      const folder = join(directory, `Item ${String(item).padStart(2, "0")}`);
      await mkdir(folder);
      await Promise.all(Array.from({ length: 6 }, (_, index) => writeFile(join(folder, `photo-${index}.jpg`), photo)));
    }
    await page.locator("input[webkitdirectory]").setInputFiles(directory);
    const dialog = page.getByRole("dialog", { name: "Set details for 25 listings" });
    const size = (item: string) => page.getByRole("textbox", { name: `Size for Item ${item}`, exact: true });
    const row = (item: string) => dialog.getByRole("listitem").filter({ has: size(item) });
    const preview = (item: string) => row(item).locator("img").first();
    const photoWidth = (item: string) => preview(item).evaluate((image: HTMLImageElement) => image.naturalWidth);

    await size("01").fill("XL");
    await expect.poll(() => photoWidth("01")).toBeGreaterThan(0);
    // The last folder must not load its large images while the first folder is in view.
    expect(await photoWidth("25")).toBe(0);

    for (const item of ["13", "25", "01"]) {
      await size(item).scrollIntoViewIfNeeded();
      await expect(size(item)).toBeInViewport();
      await preview(item).scrollIntoViewIfNeeded();
      await expect(preview(item)).toBeInViewport();
      await preview(item).evaluate((image: HTMLImageElement) => image.decode());
      await expect(dialog.getByRole("button", { name: "Create drafts only" })).toBeInViewport();
      if (item === "13") await page.screenshot({ path: info.outputPath("bulk-upload-scroll.png") });
    }
    await expect(size("01")).toHaveValue("XL");
    await dialog.getByRole("button", { name: "Cancel bulk upload" }).click();
    await expect(dialog).toBeHidden();
  } finally {
    await request.delete(`/api/conversations/${id}`);
    await rm(directory, { recursive: true, force: true });
  }
});
