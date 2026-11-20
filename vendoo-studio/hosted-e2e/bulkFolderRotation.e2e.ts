import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;
// A 4 x 6 portrait PNG: a quarter turn shows up as swapped saved dimensions.
const portrait = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAFElEQVR4nGM8ISfHAANMDEiAHA4AT/wBEFJ55UUAAAAASUVORK5CYII=", "base64");
// A 6 x 4 landscape PNG. Each fixture has its own bytes, so the test can refuse one upload alone.
const landscape = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAYAAAAECAIAAAAiZtkUAAAAFElEQVR4nGOUkzvBgAqYGDAAcUIAS/oBDEoDsgIAAAAASUVORK5CYII=", "base64");
// Jeans' own 4 x 6 portrait, never matched by the refusal.
const jeansPortrait = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAFElEQVR4nGOUW2DDAANMDEiAHA4ATDwBBjQLLX8AAAAASUVORK5CYII=", "base64");

test("hosted folder drafts keep a preview turn when the folder's first photo is refused", async ({ page, request }, info) => {
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
    // Coat: a landscape and a portrait. Its first photo is refused and its second
    // turned, so a turn paired with saved photos by position would land wrongly.
    // Jeans: untouched. Hosted storage names every photo the same, so only the
    // client's own record of which file it sent can pair the turn correctly.
    await mkdir(join(directory, "Coat"));
    await writeFile(join(directory, "Coat", "a.png"), landscape);
    await writeFile(join(directory, "Coat", "b.png"), portrait);
    await mkdir(join(directory, "Jeans"));
    await writeFile(join(directory, "Jeans", "b.png"), jeansPortrait);

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
      .filter({ has: page.getByRole("textbox", { name: "Size for Coat", exact: true }) });
    // The order inside a folder follows the file system, so read it from the preview:
    // refuse whichever photo is first and turn the second.
    const thumbs = coat.locator(".photo-thumb img");
    await expect(thumbs).toHaveCount(2);
    await expect.poll(() => thumbs.evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    const shapes = await thumbs.evaluateAll(images => images.map(image => (image as HTMLImageElement).naturalWidth > (image as HTMLImageElement).naturalHeight));
    expect(new Set(shapes).size).toBe(2);
    const firstIsLandscape = shapes[0];
    const refusedBytes = firstIsLandscape ? landscape : portrait;
    // The second photo turned a quarter: a portrait saves 6x4, a landscape 4x6.
    const coatSaved = firstIsLandscape ? "6x4" : "4x6";
    await coat.getByRole("button", { name: "Rotate photo 2 right", exact: true }).click();
    await page.route("**/api/drafts/*/photos", async route => {
      if (route.request().method() === "POST" && route.request().postDataBuffer()?.equals(refusedBytes)) {
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
    // Coat keeps only its second photo, turned; the refused first never saved.
    await expect.poll(saved, { timeout: 30_000 }).toEqual({ Coat: [coatSaved], Jeans: ["4x6"] });
  } finally {
    await page.unroute("**/api/drafts/*/photos");
    for (const draftID of [host, ...created]) await request.delete(`${api}/api/drafts/${draftID}`, { headers });
    await rm(directory, { recursive: true, force: true });
  }
});
