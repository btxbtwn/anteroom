import { crc32, deflateSync } from "node:zlib";
import type { Locator, Page } from "@playwright/test";
import { expect, openForm, openListing, test } from "./fixtures";

/** A 320×800 PNG of one color, so each upload is a different photo. */
function png(red: number, green: number, blue: number): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body));
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(320, 0);
  header.writeUInt32BE(800, 4);
  header.set([8, 2, 0, 0, 0], 8);
  const row = Buffer.from([0, ...Array.from({ length: 320 }, () => [red, green, blue]).flat()]);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(Array.from({ length: 800 }, () => row)))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

async function order(strip: Locator): Promise<string[]> {
  return strip.locator("[data-sortable-photo]").evaluateAll((tiles) => tiles.map((tile) => tile.getAttribute("data-sortable-photo") || ""));
}

/** Press and hold a photo, then drag it, the way a finger reorders on an iPad or iPhone. */
async function touchDrag(page: Page, from: Locator, to: Locator) {
  const start = (await from.boundingBox())!;
  const end = (await to.boundingBox())!;
  const client = await page.context().newCDPSession(page);
  const point = (x: number, y: number) => [{ x, y, id: 1 }];
  const [x0, y0] = [start.x + start.width / 2, start.y + start.height / 2];
  const [x1, y1] = [end.x + end.width / 4, end.y + end.height / 2];
  await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: point(x0, y0) });
  await page.waitForTimeout(500);
  for (let step = 1; step <= 10; step += 1) {
    await client.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: point(x0 + ((x1 - x0) * step) / 10, y0 + ((y1 - y0) * step) / 10) });
  }
  await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

test("photos on a marketplace form reorder by dragging, with a mouse or a finger", async ({ page, phone, request, browserName }, info) => {
  test.skip(browserName !== "chromium", "touch drags are sent through Chromium's DevTools protocol");
  test.setTimeout(120_000);
  const title = `Photo order ${info.project.name} ${Date.now()}`;
  const conversation = await (await request.post("/api/conversations", { data: { title } })).json();
  const id = conversation.id as string;
  try {
    for (const [index, color] of [[1, [200, 0, 0]], [2, [0, 200, 0]], [3, [0, 0, 200]]] as const) {
      const upload = await request.post(`/api/conversations/${id}/photos`, {
        multipart: { files: { name: `photo-${index}.png`, mimeType: "image/png", buffer: png(...color) } },
      });
      expect(upload.ok()).toBe(true);
    }
    const saved = await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Photo order", price: 20,
    } } });
    expect(saved.ok()).toBe(true);
    await page.route("**/api/settings/marketplaces", async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), selected: ["ebay", "etsy", "poshmark", "mercari", "depop"] } });
    });
    await openListing(page, phone, title);
    // A phone opens the listing on its Chat tab; the forms are on the Listing tab.
    if (phone) await page.getByRole("tab", { name: "Listing" }).click();
    await openForm(page, phone, "Depop");
    const strip = page.locator('[data-marketplace-media="depop"]');
    await expect(strip.locator("[data-sortable-photo]")).toHaveCount(3, { timeout: 30_000 });
    const [first, second, third] = await order(strip);

    const tile = (photo: string) => strip.locator(`[data-sortable-photo="${photo}"]`);
    if (phone) await touchDrag(page, tile(second), tile(first));
    else await tile(second).dragTo(tile(first), { steps: 12 });
    await expect.poll(() => order(strip)).toEqual([second, first, third]);
    // Letting go does not also open the photo.
    await expect(page.getByRole("dialog")).toHaveCount(0);

    // A quick tap still opens the photo, and its viewer moves it without dragging.
    await tile(third).click();
    const viewer = page.getByRole("dialog");
    await viewer.getByRole("button", { name: "Earlier" }).click();
    await expect.poll(() => order(strip)).toEqual([second, third, first]);

    await expect(viewer.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
    const revert = viewer.getByRole("button", { name: "Revert to original" });
    await expect(revert).toHaveCount(0);
    await viewer.getByRole("button", { name: "Rotate right" }).click();
    await expect(revert).toBeEnabled();
    await revert.click();
    await expect(revert).toHaveCount(0);
    await expect(viewer.getByRole("button", { name: "Rotate left" })).toBeEnabled();
    await viewer.getByRole("button", { name: "Edit photo" }).click();
    const crop = page.getByRole("dialog", { name: /^Edit photo/ });
    await crop.getByRole("button", { name: "Square" }).click();
    const frame = crop.locator("[data-crop-frame]");
    const [visibleFrame, frameBounds] = await Promise.all([
      frame.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        const stage = element.closest("div.overflow-hidden")!.getBoundingClientRect();
        return bounds.top >= stage.top && bounds.bottom <= stage.bottom;
      }),
      frame.boundingBox(),
    ]);
    expect(visibleFrame).toBe(true);
    expect(frameBounds?.height).toBeGreaterThan(100);
    const moveCrop = crop.getByRole("button", { name: "Move crop with arrow keys" });
    const beforeMove = (await moveCrop.boundingBox())!.y;
    await moveCrop.press("Shift+ArrowUp");
    expect((await moveCrop.boundingBox())!.y).toBeLessThan(beforeMove);
    const beforeResize = (await moveCrop.boundingBox())!.width;
    const corner = crop.getByRole("button", { name: "Resize from bottom right" });
    await corner.press("Shift+ArrowLeft");
    const afterHorizontal = (await moveCrop.boundingBox())!.width;
    expect(afterHorizontal).toBeLessThan(beforeResize);
    await corner.press("Shift+ArrowUp");
    expect((await moveCrop.boundingBox())!.width).toBeLessThan(afterHorizontal);
    await crop.getByRole("button", { name: "Square" }).click();
    await info.attach("photo-crop", { body: await crop.screenshot(), contentType: "image/png" });
    const savedCrop = page.waitForResponse((response) =>
      response.request().method() === "PUT" && response.url().endsWith(`/photos/${third}/edits`),
    );
    await crop.getByRole("button", { name: "Save" }).click();
    expect(await (await savedCrop).json()).toMatchObject({ width: 320, height: 320 });
    await expect(revert).toBeEnabled();
    await revert.click();
    await expect(revert).toHaveCount(0);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
