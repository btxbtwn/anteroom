import { deflateSync } from "node:zlib";
import { expect, openForm, openListing, showStatusTab, test } from "./fixtures";

/** PNG chunk checksum, including on Node versions before zlib exposed crc32. */
function crc32(bytes: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

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

test("bulk AI Studio reviews covers across drafts and saves each selected photo independently", async ({ page, phone, request, createConversation }, info) => {
  test.setTimeout(90_000);
  const drafts = await Promise.all(["Olive field jacket", "Straight-leg blue jeans"].map(async (title) => {
    const { id } = await (await createConversation({ title, notes: "" })).json();
    for (const [index, color] of [[1, [45, 110, 85]], [2, [30, 60, 160]]] as const) {
      const response = await request.post(`/api/conversations/${id}/photos`, {
        multipart: { files: { name: `photo-${index}.png`, mimeType: "image/png", buffer: png(...color) } },
      });
      expect(response.ok()).toBe(true);
    }
    return { id, title };
  }));
  const otherTitle = "Wool cardigan";
  const { id: otherId } = await (await createConversation({ title: otherTitle, notes: "" })).json();
  const otherUpload = await request.post(`/api/conversations/${otherId}/photos`, {
    multipart: { files: { name: "cardigan.png", mimeType: "image/png", buffer: png(160, 70, 30) } },
  });
  expect(otherUpload.ok()).toBe(true);

  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await showStatusTab(page, "Draft");
  const startBatch = async (titles: string[]) => {
    await page.getByRole("button", { name: "Select listings", exact: true }).click();
    for (const title of titles) await page.getByRole("checkbox", { name: `Select ${title}`, exact: true }).click();
    await page.getByRole("region", { name: "Bulk listing actions" }).getByRole("button", { name: "More", exact: true }).click();
    await page.getByRole("menuitem", { name: "Edit photos" }).click();
  };
  await startBatch(drafts.map((draft) => draft.title));

  const dialog = page.getByRole("dialog", { name: "Edit photos" });
  await dialog.getByRole("textbox", { name: "Keep in cutout" }).fill("garment");
  await dialog.getByRole("textbox", { name: "Leave out of cutout" }).fill("stand");
  await dialog.getByRole("button", { name: "Review photos" }).click();
  const cover = (title: string) => dialog.getByRole("group", { name: title }).getByRole("checkbox", { name: "Cover photo" });
  const detail = (title: string) => dialog.getByRole("group", { name: title }).getByRole("checkbox", { name: "Photo 2" });
  for (const draft of drafts) {
    await expect(cover(draft.title)).toBeChecked();
    await expect(detail(draft.title)).not.toBeChecked();
  }
  await dialog.getByRole("button", { name: "All photos" }).click();
  await expect(detail(drafts[0].title)).toBeChecked();
  await detail(drafts[0].title).uncheck();
  await expect(dialog.getByRole("button", { name: "Apply to 3 photos" })).toBeEnabled();
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await expect(page.locator("html")).toHaveCSS("color-scheme", theme);
    const path = info.outputPath(`bulk-photo-review-${theme}.png`);
    await page.screenshot({ path, animations: "disabled" });
    await info.attach(`bulk-photo-review-${theme}`, { path, contentType: "image/png" });
  }

  const photos = async (id: string) => (await request.get(`/api/conversations/${id}/photos`)).json();
  const before = await photos(drafts[1].id);
  expect((await request.delete(`/api/conversations/${drafts[1].id}/photos/${before[1].id}`)).ok()).toBe(true);
  const results = page.getByRole("dialog", { name: "Photo edit results" });
  // Hold the first batch while a second one saves, then drop one reply after
  // the real sandbox route has saved the first batch's edit.
  let resume = () => {};
  const waiting = new Promise<void>((resolve) => { resume = resolve; });
  await page.route("**/photos/studio", async (route) => {
    const request = route.request();
    const firstBatch = request.postDataJSON().options.keep === "garment";
    if (firstBatch) await waiting;
    if (firstBatch && request.url().endsWith(`/conversations/${drafts[0].id}/photos/studio`)) {
      expect((await route.fetch()).ok()).toBe(true);
      await route.abort();
    } else {
      await route.continue();
    }
  });
  try {
    await dialog.getByRole("button", { name: "Apply to 3 photos" }).click();
    await results.getByRole("button", { name: "Continue in background" }).click();
    await startBatch([drafts[0].title, otherTitle]);
    await dialog.getByRole("button", { name: "Review photos" }).click();
    await dialog.getByRole("button", { name: "Apply to 2 photos" }).click();
    await expect(results.getByText("1 saved · 0 failed · 1 skipped", { exact: true })).toBeVisible();
    await expect(results.getByText(/Already editing in another batch/)).toBeVisible();
    expect((await photos(otherId))[0].edits?.studio).toBeTruthy();
    expect((await photos(drafts[0].id))[0].edits?.studio).toBeFalsy();
    const parallelResults = info.outputPath("bulk-photo-parallel-results.png");
    await page.screenshot({ path: parallelResults, animations: "disabled" });
    await info.attach("bulk-photo-parallel-results", { path: parallelResults, contentType: "image/png" });
    await results.getByRole("button", { name: "Done" }).click();
    await page.getByRole("link", { name: otherTitle, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`listing=${otherId}`));
    await page.getByRole("button", { name: "Photo edits · 0 of 3" }).click();
  } finally {
    resume();
  }
  await expect(results.getByText("1 saved · 0 failed · 2 skipped", { exact: true })).toBeVisible();
  await expect(results.getByText(/Couldn't confirm the edit/)).toBeVisible();
  await expect(results.getByText("Photo 2 · Photo not found", { exact: true })).toBeVisible();

  const first = await photos(drafts[0].id);
  expect(first[0].edits?.studio?.options).toMatchObject({ keep: "garment", exclude: "stand" });
  expect(first[1].edits?.studio).toBeFalsy();
  expect((await photos(drafts[1].id))[0].edits?.studio?.options).toMatchObject({ keep: "garment", exclude: "stand" });
  await results.getByRole("button", { name: "Review again" }).click();
  await expect(cover(drafts[0].title)).toBeVisible();
  await expect(cover(drafts[0].title)).not.toBeChecked();
  await expect(dialog.getByRole("button", { name: "Apply to 0 photos" })).toBeDisabled();
  const recovery = info.outputPath("bulk-photo-recovery.png");
  await page.screenshot({ path: recovery, animations: "disabled" });
  await info.attach("bulk-photo-recovery", { path: recovery, contentType: "image/png" });

  const reset = await request.delete(`/api/conversations/${drafts[1].id}/photos/${before[0].id}/edits`);
  expect(reset.ok()).toBe(true);
  expect((await reset.json()).url).toBe(before[0].url);
});

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
  // The Markup preview reloads after the tab switch; draw on the settled frame.
  const settled = async () => {
    await expect(editor.getByText(/^(Updating|Loading photo)…$/)).toHaveCount(0);
    return (await frame.boundingBox())!;
  };
  let box = await settled();
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.8);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.55, { steps: 6 });
  await page.mouse.up();
  await expect(editor.getByText("Arrow 1")).toBeVisible();
  await editor.getByRole("button", { name: "Pixelate" }).click();
  box = await settled();
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

test("AI Studio lets the seller open another draft while an edit runs", async ({ page, phone, request, createConversation }, info) => {
  const title = `Background studio ${info.project.name} ${Date.now()}`;
  const other = `Other draft ${info.project.name} ${Date.now()}`;
  const { id } = await (await createConversation({ title, notes: "" })).json();
  await createConversation({ title: other, notes: "" });
  expect((await request.post(`/api/conversations/${id}/photos`, {
    multipart: { files: { name: "photo.png", mimeType: "image/png", buffer: png(30, 60, 160) } },
  })).ok()).toBe(true);
  expect((await request.put(`/api/conversations/${id}/listing`, {
    data: { expected_revision_id: "", listing: { title, description: "AI Studio background", price: 20 } },
  })).ok()).toBe(true);
  await page.route("**/api/settings/marketplaces", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), selected: ["depop"] } });
  });
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route(`**/conversations/${id}/photos/studio`, async (route) => {
    await held;
    await route.continue();
  });
  await openListing(page, phone, title);
  if (phone) await page.getByRole("tab", { name: "Listing" }).click();
  await openForm(page, phone, "Depop");
  await page.locator('[data-marketplace-media="depop"] [data-sortable-photo]').first().click();
  await page.getByRole("dialog").getByRole("button", { name: "AI Studio" }).click();
  await expect(page.getByRole("img", { name: "Photo 1 preview", exact: true })).toBeVisible();
  const settled = page.waitForResponse((response) => response.url().endsWith(`/conversations/${id}/photos/studio`));
  try {
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    const leave = page.getByRole("button", { name: "Continue in background" });
    await expect(leave).toBeEnabled();
    await page.screenshot({ path: info.outputPath("ai-studio-background.png") });
    await leave.click();
    await expect(page.getByRole("dialog", { name: /^(Edit photo |Photo \d+ of )/ })).toHaveCount(0);
    await expect(page.getByText("AI Studio is editing photos")).toBeVisible();
    if (phone) await page.getByRole("banner").getByRole("button", { name: "Inventory", exact: true }).click();
    await page.getByRole("searchbox", { name: /^Search listings/ }).fill(other);
    await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: other, exact: true }).click();
  } finally {
    release();
  }
  expect((await (await settled).json()).errors).toEqual([]);
  await expect(page.getByText("AI Studio edits saved")).toBeVisible();
  const saved = await (await request.get(`/api/conversations/${id}/photos`)).json();
  expect(saved[0].edits.studio).toBeTruthy();
});

test("drawing on a photo never selects page text, so a quick second mark is not dragged away", async ({ page, phone, request, createConversation }) => {
  test.skip(phone, "A mouse drag; phones draw with touch.");
  const title = `Markup selection ${Date.now()}`;
  const id = (await (await createConversation({ title, notes: "" })).json()).id as string;
  const upload = await request.post(`/api/conversations/${id}/photos`, {
    multipart: { files: { name: "photo.png", mimeType: "image/png", buffer: png(30, 60, 160) } },
  });
  expect(upload.ok()).toBe(true);
  expect((await request.put(`/api/conversations/${id}/listing`, { data: { expected_revision_id: "", listing: {
    title, description: "Markup", price: 20,
  } } })).ok()).toBe(true);
  await page.route("**/api/settings/marketplaces", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), selected: ["depop"] } });
  });
  await openListing(page, phone, title);
  await openForm(page, phone, "Depop");
  const strip = page.locator('[data-marketplace-media="depop"]');
  await expect(strip.locator("[data-sortable-photo]")).toHaveCount(1, { timeout: 30_000 });
  await strip.locator("[data-sortable-photo]").first().click();
  await page.getByRole("dialog").getByRole("button", { name: "Edit photo" }).click();
  const editor = page.getByRole("dialog", { name: /^Edit photo/ });
  await editor.getByRole("button", { name: "Markup" }).click();
  await expect(editor.getByText(/^(Updating|Loading photo)…$/)).toHaveCount(0);
  const box = (await editor.locator("[data-markup-frame]").boundingBox())!;
  const selected = () => page.evaluate(() => document.getSelection()?.isCollapsed === false);
  const drag = async (x1: number, y1: number, x2: number, y2: number) => {
    await page.mouse.move(box.x + box.width * x1, box.y + box.height * y1);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * x2, box.y + box.height * y2, { steps: 6 });
    await page.mouse.up();
  };
  // Text selected by one drag is what the browser drags away (pointercancel) on the next one.
  await editor.getByRole("button", { name: "Pixelate", exact: true }).click();
  await drag(0.55, 0.1, 0.9, 0.2);
  await expect(editor.getByText("Pixelate 1")).toBeVisible();
  expect(await selected()).toBe(false);
  await editor.getByRole("button", { name: "Arrow", exact: true }).click();
  await drag(0.2, 0.8, 0.45, 0.55);
  await expect(editor.getByText("Arrow 1")).toBeVisible();
  expect(await selected()).toBe(false);
});
