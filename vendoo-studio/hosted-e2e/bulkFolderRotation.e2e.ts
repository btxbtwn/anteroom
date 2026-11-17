import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;
// A 4 x 6 portrait PNG: a quarter turn shows up as swapped saved dimensions.
const portrait = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAFElEQVR4nGM8ISfHAANMDEiAHA4AT/wBEFJ55UUAAAAASUVORK5CYII=", "base64");
// A 6 x 4 landscape PNG with different bytes, so the test can refuse this upload alone.
const refusedLandscape = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAYAAAAECAIAAAAiZtkUAAAAFElEQVR4nGOUkzvBgAqYGDAAcUIAS/oBDEoDsgIAAAAASUVORK5CYII=", "base64");

test("hosted folder drafts keep a preview turn when another photo in the folder is refused", async ({ page, request }, info) => {
  test.setTimeout(120_000);
  const login = await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const title = `Hosted bulk rotation ${info.project.name} ${Date.now()}`;
  const opened = await request.post(`${api}/api/drafts`, { headers, data: { title } });
  expect(opened.status()).toBe(201);
  const host = (await opened.json()).id as string;
  const directory = await mkdtemp(join(tmpdir(), "hosted-bulk-rotation-"));
  const created: string[] = [];
  let refused = 0;
  try {
    // Coat: a refused landscape photo and a portrait photo turned in the preview.
    // Jeans: the same portrait, untouched. Hosted storage names every photo the
    // same, so only the client's own record of which file it sent can pair the
    // turn with the saved photo and keep it off the refused one.
    await mkdir(join(directory, "Coat"));
    await writeFile(join(directory, "Coat", "a.png"), refusedLandscape);
    await writeFile(join(directory, "Coat", "b.png"), portrait);
    await mkdir(join(directory, "Jeans"));
    await writeFile(join(directory, "Jeans", "b.png"), portrait);

    await page.goto("/?sign-in");
    await page.getByLabel("Email address").fill("alice@sandbox.test");
    await page.getByLabel("Password").fill("sandbox");
    const signedIn = page.waitForResponse(response => response.url().includes("/auth/v1/token") && response.ok());
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await signedIn;
    await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeHidden();
    await page.goto(`/?listing=${host}`);
    if (info.project.name === "phone") await page.getByRole("tab", { name: "Listing" }).click();
    await page.locator("input[webkitdirectory]").setInputFiles(directory);
    const dialog = page.getByRole("dialog", { name: "Set details for 2 listings" });
    const coat = dialog.locator("li.bulk-upload-group")
      .filter({ has: page.getByRole("textbox", { name: "Size tag for Coat", exact: true }) });
    // The order inside a folder follows the file system, so find the portrait by its pixels.
    const thumbs = coat.locator(".photo-thumb img");
    await expect(thumbs).toHaveCount(2);
    await expect.poll(() => thumbs.evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    const shapes = await thumbs.evaluateAll(images => images.map(image => (image as HTMLImageElement).naturalWidth < (image as HTMLImageElement).naturalHeight));
    const portraitIndex = shapes.indexOf(true);
    expect(portraitIndex).toBeGreaterThanOrEqual(0);
    await coat.getByRole("button", { name: `Rotate photo ${portraitIndex + 1} right`, exact: true }).click();
    await page.route("**/api/drafts/*/photos", async route => {
      if (route.request().method() === "POST" && route.request().postDataBuffer()?.equals(refusedLandscape)) {
        refused += 1;
        return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Storage is unavailable." }) });
      }
      return route.continue();
    });
    page.on("response", async response => {
      if (response.request().method() === "POST" && response.url().endsWith("/api/conversations") && response.ok()) {
        created.push((await response.json()).id);
      }
    });
    await dialog.getByRole("button", { name: "Create drafts only" }).click();
    await expect.poll(() => created.length, { timeout: 30_000 }).toBe(2);
    await expect.poll(() => refused, { timeout: 30_000 }).toBe(1);
    const saved = async () => Object.fromEntries(await Promise.all(created.map(async draftID => {
      const draft = await (await request.get(`${api}/api/drafts/${draftID}`, { headers })).json();
      const photos = await (await request.get(`${api}/api/conversations/${draftID}/photos`, { headers })).json();
      return [draft.title, photos.map((photo: { width: number; height: number }) => `${photo.width}x${photo.height}`)];
    })));
    // Only Coat's portrait was turned; the refused landscape never saved, and no turn moved onto it.
    await expect.poll(saved, { timeout: 30_000 }).toEqual({ Coat: ["6x4"], Jeans: ["4x6"] });
  } finally {
    await page.unroute("**/api/drafts/*/photos");
    for (const draftID of [host, ...created]) await request.delete(`${api}/api/drafts/${draftID}`, { headers });
    await rm(directory, { recursive: true, force: true });
  }
});
