import { crc32, deflateSync } from "node:zlib";
import { expect, openForm, openListing, test } from "./fixtures";

/** A 320×400 PNG: a coloured block on a grey backdrop, the shape of a product shot. */
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
  header.writeUInt32BE(400, 4);
  header.set([8, 2, 0, 0, 0], 8);
  const rows = Array.from({ length: 400 }, (_, y) => Buffer.from([0, ...Array.from({ length: 320 }, (_, x) =>
    (x > 80 && x < 240 && y > 80 && y < 320 ? [red, green, blue] : [200, 196, 188])).flat()]));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

test("AI Studio edits several photos at once offline, markup and filters save, and Revert brings the upload back", async ({ page, phone, request, createConversation }, info) => {
  test.setTimeout(120_000);
  const title = `AI Studio ${info.project.name} ${Date.now()}`;
  const id = (await (await createConversation({ title, notes: "" })).json()).id as string;
  for (const [index, color] of [[1, [30, 60, 160]], [2, [160, 40, 40]]] as const) {
    const upload = await request.post(`/api/conversations/${id}/photos`, {
      multipart: { files: { name: `photo-${index}.png`, mimeType: "image/png", buffer: png(...color) } },
    });
    expect(upload.ok()).toBe(true);
  }
  expect((await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
    title, description: "AI Studio", price: 20,
  } } })).ok()).toBe(true);
  await page.route("**/api/settings/marketplaces", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), selected: ["ebay", "etsy", "poshmark", "mercari", "depop"] } });
  });
  await openListing(page, phone, title);
  if (phone) await page.getByRole("tab", { name: "Listing" }).click();
  await openForm(page, phone, "Depop");
  const strip = page.locator('[data-marketplace-media="depop"]');
  await expect(strip.locator("[data-sortable-photo]")).toHaveCount(2, { timeout: 30_000 });
  await strip.locator("[data-sortable-photo]").first().click();

  const viewer = page.getByRole("dialog");
  await viewer.getByRole("button", { name: "AI Studio" }).click();
  const editor = page.getByRole("dialog", { name: /^Edit photo/ });
  await editor.getByText("Ghost mannequin").click();
  await editor.getByText("Natural shadow").click();
  await editor.getByRole("button", { name: "Select all photos" }).click();
  await info.attach("ai-studio", { body: await page.screenshot(), contentType: "image/png" });
  const studio = page.waitForResponse((response) => response.url().endsWith(`/conversations/${id}/photos/studio`));
  await editor.getByRole("button", { name: "Apply to 2 photos" }).click();
  const result = await (await studio).json();
  expect(result.errors).toEqual([]);
  expect(result.photos).toHaveLength(2);
  expect(result.photos[0].edits.studio.options).toMatchObject({ ghost_mannequin: true, shadow: true, remove_background: true });
  await expect(editor).toHaveCount(0);

  // Back in the viewer: open the editor, mark a flaw, hide a name, add a filter and a border.
  await viewer.getByRole("button", { name: "Edit photo" }).click();
  await editor.getByRole("button", { name: "Markup" }).click();
  const frame = editor.locator("[data-markup-frame]");
  await expect(frame).toBeVisible();
  const box = (await frame.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.8);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.55, { steps: 6 });
  await page.mouse.up();
  await editor.getByRole("button", { name: "Pixelate" }).click();
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.1);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.9, box.y + box.height * 0.2, { steps: 6 });
  await page.mouse.up();
  await expect(editor.getByText("Arrow 1")).toBeVisible();
  await expect(editor.getByText("Pixelate 1")).toBeVisible();
  await info.attach("markup", { body: await page.screenshot(), contentType: "image/png" });
  await editor.getByRole("button", { name: "Adjust" }).click();
  await editor.getByRole("button", { name: "Warm" }).click();
  await editor.getByRole("slider", { name: "Border width" }).fill("6");
  await info.attach("adjust", { body: await page.screenshot(), contentType: "image/png" });
  const saved = page.waitForResponse((response) => response.request().method() === "PUT" && response.url().includes("/edits"));
  await editor.getByRole("button", { name: "Save" }).click();
  const body = await (await saved).json();
  expect(body.edits.studio).toBeTruthy();
  expect(body.edits.annotations).toHaveLength(1);
  expect(body.edits.redactions).toHaveLength(1);
  expect(body.edits.filter).toBe("warm");

  const revert = viewer.getByRole("button", { name: "Revert to original" });
  await revert.click();
  await expect(revert).toHaveCount(0);
  const photos = await (await request.get(`/api/conversations/${id}/photos`)).json();
  expect(photos[0].edits).toBeNull();
  expect([photos[0].width, photos[0].height]).toEqual([320, 400]);
});
