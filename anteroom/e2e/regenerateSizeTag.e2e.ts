import { createListing, expect, showStatusTab, test } from "./fixtures";

test("regenerate review reads size tags, checks saved sizes and saves edits before rewriting", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const suffix = `${info.project.name} ${Date.now()}`;
  const titles = {
    shorts: `Regen Shorts ${suffix}`,
    jeans: `Regen Jeans ${suffix}`,
    tee: `Regen Tee ${suffix}`,
  };
  const [shortsId, jeansId, teeId] = await Promise.all([
    createListing(request, titles.shorts),
    createListing(request, titles.jeans),
    createListing(request, titles.tee),
  ]);
  const ids = [shortsId, jeansId, teeId];
  const tags: Record<string, object> = {
    [shortsId]: { photos_read: 1, status: "accepted", size: "0", sizes: ["0"] },
    [jeansId]: { photos_read: 1, status: "accepted", size: "32 x 30", sizes: ["32 x 30"] },
    [teeId]: { photos_read: 1, status: "conflict", size: null, sizes: ["M", "L"] },
  };
  const generated = new Set<string>();

  try {
    await request.patch(`/api/conversations/${jeansId}`, {
      data: { notes: JSON.stringify({ size: "34 x 30", sku: "JEANS-1" }) },
    });

    await page.route("**/api/conversations/*/size-tags/read", async (route) => {
      const id = new URL(route.request().url()).pathname.split("/")[3]!;
      await route.fulfill({ json: tags[id] });
    });
    await page.route("**/api/conversations/*/generate", async (route) => {
      const id = new URL(route.request().url()).pathname.split("/")[3]!;
      generated.add(id);
      await route.fulfill({ contentType: "text/event-stream", body: 'data: {"title":"Rewritten draft"}\n\ndata: [DONE]\n\n' });
    });

    await page.goto("/");
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    }
    await showStatusTab(page, "Draft");
    await page.getByRole("button", { name: "Select listings", exact: true }).click();
    for (const title of Object.values(titles)) {
      await page.getByRole("list", { name: "Listings" }).getByRole("checkbox", { name: `Select ${title}`, exact: true }).click();
    }
    await page.getByRole("region", { name: "Bulk listing actions" }).getByRole("button", { name: "More", exact: true }).click();
    await page.getByRole("menuitem", { name: "Regenerate…", exact: true }).click();

    const dialog = page.getByRole("dialog", { name: "Review 3 listings before rewriting" });
    const size = (title: string) => dialog.getByRole("textbox", { name: `Size for ${title}`, exact: true });

    await expect(size(titles.jeans)).toHaveValue("34 x 30");
    await expect(size(titles.shorts)).toHaveValue("");
    await expect(size(titles.tee)).toHaveValue("");

    await dialog.getByRole("button", { name: "Read size tags", exact: true }).click();
    await expect(size(titles.shorts)).toHaveValue("0");
    await expect(size(titles.jeans)).toHaveValue("34 x 30");
    await expect(dialog.getByText("The tag reads 32 x 30. Check the size you typed.")).toBeVisible();
    await expect(size(titles.tee)).toHaveValue("");
    await expect(dialog.getByText("The photos show different sizes (M, L). Check the tag and type the size.")).toBeVisible();
    await expect(dialog).not.toContainText(/openrouter|gemini|google/i);

    await page.screenshot({ path: info.outputPath(`regenerate-size-tags-${info.project.name}.png`) });

    await size(titles.tee).fill("M");
    await dialog.getByRole("button", { name: "Rewrite 3", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect.poll(() => generated.size, { timeout: 30_000 }).toBe(3);

    const saved = await Promise.all(ids.map(async (id) => (await request.get(`/api/conversations/${id}`)).json()));
    const notesById = Object.fromEntries(saved.map((conv) => [conv.id, JSON.parse(conv.notes)]));
    expect(notesById[shortsId].size).toBe("0");
    expect(notesById[jeansId]).toMatchObject({ size: "34 x 30", sku: "JEANS-1" });
    expect(notesById[teeId].size).toBe("M");
  } finally {
    await page.unrouteAll({ behavior: "wait" });
    await Promise.allSettled(ids.map((id) => request.delete(`/api/conversations/${id}`)));
  }
});
