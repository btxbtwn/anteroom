import { crc32, deflateSync } from "node:zlib";
import type { Locator, Page } from "@playwright/test";
import { expect, openForm, openListing, test } from "./fixtures";

/** A 1x1 PNG of one color, so each upload is a different photo. */
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
  header.writeUInt32BE(1, 0);
  header.writeUInt32BE(1, 4);
  header.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.from([0, red, green, blue]))),
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
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
