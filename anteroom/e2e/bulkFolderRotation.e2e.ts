import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createListing, expect, openListing, test } from "./fixtures";

// A 4 x 6 portrait PNG, so a quarter turn shows up as swapped saved dimensions.
const portrait = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAFElEQVR4nGM8ISfHAANMDEiAHA4AT/wBEFJ55UUAAAAASUVORK5CYII=", "base64");

test("a photo turned in the folder preview is saved turned on its own draft", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const title = `Bulk rotation ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const directory = await mkdtemp(join(tmpdir(), "bulk-rotation-"));
  const created: string[] = [];
  try {
    // The same file name in both folders: each folder's turn must stay with its own photo.
    for (const name of ["Coat", "Jeans"]) {
      await mkdir(join(directory, name));
      await writeFile(join(directory, name, "front.png"), portrait);
    }
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    await page.locator("input[webkitdirectory]").setInputFiles(directory);
    const dialog = page.getByRole("dialog", { name: "Set details for 2 listings" });
    const coat = dialog.locator("li.bulk-upload-group").filter({ has: page.getByRole("textbox", { name: "Size for Coat", exact: true }) });
    await coat.getByRole("button", { name: "Rotate photo 1 right", exact: true }).click();
    page.on("response", async response => {
      if (response.request().method() === "POST" && response.url().endsWith("/api/conversations") && response.ok()) {
        created.push((await response.json()).id);
      }
    });
    await dialog.getByRole("button", { name: "Create drafts only" }).click();
    await expect(dialog).toBeHidden();
    await expect.poll(() => created.length, { timeout: 30_000 }).toBe(2);
    const saved = async () => Object.fromEntries(await Promise.all(created.map(async draftID => {
      const draft = await (await request.get(`/api/conversations/${draftID}`)).json();
      const photos = await (await request.get(`/api/conversations/${draftID}/photos`)).json();
      return [draft.title, photos.map((photo: { width: number; height: number }) => `${photo.width}x${photo.height}`)];
    })));
    await expect.poll(saved, { timeout: 30_000 }).toEqual({ Coat: ["6x4"], Jeans: ["4x6"] });
  } finally {
    for (const draftID of [id, ...created]) await request.delete(`/api/conversations/${draftID}`);
    await rm(directory, { recursive: true, force: true });
  }
});

test("cancels one bulk item, retries it, and rates a generated draft", async ({ page, phone, request }, info) => {
  test.setTimeout(120_000);
  const title = `Bulk generation ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  const directory = await mkdtemp(join(tmpdir(), "bulk-generate-"));
  const created = new Map<string, string>();
  try {
    for (const name of ["Coat", "Jeans"]) {
      await mkdir(join(directory, name));
      await writeFile(join(directory, name, "front.png"), portrait);
    }
    await openListing(page, phone, title);
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    await page.locator("input[webkitdirectory]").setInputFiles(directory);
    await page.getByRole("textbox", { name: "SKU for Jeans" }).fill("[sandbox:stall]");
    page.on("response", async response => {
      if (response.request().method() === "POST" && response.url().endsWith("/api/conversations") && response.ok()) {
        const draft = await response.json();
        created.set(draft.title, draft.id);
      }
    });
    await page.getByRole("button", { name: "Create and generate 2" }).click();
    const progress = page.getByRole("dialog", { name: "Bulk generation" });
    const jeans = progress.getByRole("listitem").filter({ has: page.getByRole("button", { name: "Jeans", exact: true }) });
    await jeans.getByRole("button", { name: "Cancel" }).click();
    await expect(jeans.getByRole("button", { name: "Retry" })).toBeVisible();
    await expect(progress.getByText("Draft saved")).toHaveCount(1, { timeout: 45_000 });
    expect(created.get("Jeans")).toBeTruthy();
    const edited = await request.patch(`/api/conversations/${created.get("Jeans")}`, { data: { notes: "{}" } });
    expect(edited.ok()).toBe(true);
    await jeans.getByRole("button", { name: "Retry" }).click();
    await expect(progress.getByText("Draft saved")).toHaveCount(2, { timeout: 45_000 });
    await page.screenshot({ path: info.outputPath("bulk-generation.png") });
    await progress.getByRole("button", { name: "Coat" }).click();
    await page.getByRole("button", { name: "Thumbs down" }).click();
    const feedback = page.getByRole("dialog", { name: "What needs improvement?" });
    await feedback.getByRole("button", { name: "Missed flaw" }).click();
    await feedback.getByRole("textbox", { name: "Optional feedback note" }).fill("Check the seam.");
    await feedback.getByRole("button", { name: "Save feedback" }).click();
    await expect(page.getByRole("button", { name: "Thumbs down" })).toHaveAttribute("aria-pressed", "true");
    for (const draftId of created.values()) {
      const result = await request.get(`/api/conversations/${draftId}`);
      expect((await result.json()).status).toBe("draft");
    }
  } finally {
    for (const draftId of [id, ...created.values()]) await request.delete(`/api/conversations/${draftId}`);
    await rm(directory, { recursive: true, force: true });
  }
});
