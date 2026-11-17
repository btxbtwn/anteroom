import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;
// 4 x 6 portrait PNGs: a quarter turn shows up as swapped saved dimensions.
// The blue one has different bytes, so the test can refuse that upload alone.
const red = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAFElEQVR4nGM8ISfHAANMDEiAHA4AT/wBEFJ55UUAAAAASUVORK5CYII=", "base64");
const blue = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAGCAIAAABrW6giAAAAFElEQVR4nGOUkzvBAANMDEiAHA4ATqgBECBfQqkAAAAASUVORK5CYII=", "base64");

test("hosted folder drafts keep preview turns when one upload is refused", async ({ page, request }, info) => {
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
    // Hosted storage names every photo the same, so only the client's own
    // record of which file it sent can pair a turn with the saved photo.
    for (const [name, bytes] of [["Coat", red], ["Jeans", red], ["Shirt", blue]] as const) {
      await mkdir(join(directory, name));
      await writeFile(join(directory, name, "front.png"), bytes);
    }
    await page.goto("/?sign-in");
    await page.getByLabel("Email address").fill("alice@sandbox.test");
    await page.getByLabel("Password").fill("sandbox");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.goto(`/?listing=${host}`);
    if (info.project.name === "phone") await page.getByRole("tab", { name: "Listing" }).click();
    await page.locator("input[webkitdirectory]").setInputFiles(directory);
    const dialog = page.getByRole("dialog", { name: "Set details for 3 listings" });
    for (const folder of ["Coat", "Shirt"]) {
      await dialog.locator("li.bulk-upload-group")
        .filter({ has: page.getByRole("textbox", { name: `Size tag for ${folder}`, exact: true }) })
        .getByRole("button", { name: "Rotate photo 1 right", exact: true }).click();
    }
    // Refuse Shirt's upload: Coat's turn must still land, and Jeans must stay untouched.
    await page.route("**/api/drafts/*/photos", async route => {
      if (route.request().method() === "POST" && route.request().postDataBuffer()?.equals(blue)) {
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
    await expect.poll(() => created.length, { timeout: 30_000 }).toBe(3);
    expect(refused).toBe(1);
    const saved = async () => Object.fromEntries(await Promise.all(created.map(async draftID => {
      const draft = await (await request.get(`${api}/api/drafts/${draftID}`, { headers })).json();
      const photos = await (await request.get(`${api}/api/conversations/${draftID}/photos`, { headers })).json();
      return [draft.title, photos.map((photo: { width: number; height: number }) => `${photo.width}x${photo.height}`)];
    })));
    await expect.poll(saved, { timeout: 30_000 }).toEqual({ Coat: ["6x4"], Jeans: ["4x6"], Shirt: [] });
  } finally {
    await page.unroute("**/api/drafts/*/photos");
    for (const draftID of [host, ...created]) await request.delete(`${api}/api/drafts/${draftID}`, { headers });
    await rm(directory, { recursive: true, force: true });
  }
});
