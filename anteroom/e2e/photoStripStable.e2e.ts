import { crc32, deflateSync } from "node:zlib";
import type { Locator } from "@playwright/test";
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

test("the photo strip stays put while the form re-checks after a reorder", async ({ page, phone, request, browserName }, info) => {
  test.skip(browserName !== "chromium" || phone, "the mouse drag is Chromium desktop; the layout is what is checked");
  test.setTimeout(120_000);
  const title = `Photo strip ${info.project.name} ${Date.now()}`;
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
      title, description: "Photo strip", price: 20,
    } } });
    expect(saved.ok()).toBe(true);
    await page.route("**/api/settings/marketplaces", async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...await response.json(), selected: ["ebay", "etsy", "poshmark", "mercari", "depop"] } });
    });
    // Hold the field re-check a reorder starts, so its whole window can be watched.
    let hold = false;
    let held = 0;
    await page.route((url) => url.pathname.endsWith("/native-marketplaces/preflight"), async (route) => {
      if (hold) {
        held += 1;
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      await route.continue();
    });
    await openListing(page, phone, title);
    await openForm(page, phone, "Depop");
    const strip = page.locator('[data-marketplace-media="depop"]');
    await expect(strip.locator("[data-sortable-photo]")).toHaveCount(3, { timeout: 30_000 });
    // The field check has answered and the pane above the strip is settled.
    await expect(page.getByText(/to fill|Ready/).first()).toBeVisible({ timeout: 30_000 });
    const top = async () => Math.round((await strip.boundingBox())!.y);
    const before = await top();
    const [first, second, third] = await order(strip);

    hold = true;
    const tile = (photo: string) => strip.locator(`[data-sortable-photo="${photo}"]`);
    await tile(second).dragTo(tile(first), { steps: 12 });
    await expect.poll(() => order(strip)).toEqual([second, first, third]);
    await expect.poll(() => held, { timeout: 10_000 }).toBeGreaterThan(0);
    // While the re-check runs, the strip must not move: a tap aimed at a photo has to land on it.
    const tops: number[] = [];
    for (let sample = 0; sample < 10; sample += 1) {
      tops.push(await top());
      await page.waitForTimeout(100);
    }
    expect(tops).toEqual(tops.map(() => before));
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
