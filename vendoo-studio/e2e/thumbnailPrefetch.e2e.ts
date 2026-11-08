import { expect, test } from "./fixtures";

test("loads upcoming thumbnails before their inventory rows mount", async ({ page, phone }, info) => {
  const requested = new Set<number>();
  await page.route("**/api/conversations", route => route.fulfill({ json: Array.from({ length: 120 }, (_, index) => ({
    id: `prefetch-${index}`, title: `Thumbnail ${String(index).padStart(3, "0")}`,
    status: "draft", created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z",
    cover_photo_url: `/api/prefetch-test/${index}.svg`,
  })) }));
  await page.route("**/api/prefetch-test/*.svg", route => {
    requested.add(Number(route.request().url().match(/\/(\d+)\.svg/)![1]));
    return route.fulfill({ contentType: "image/svg+xml", headers: { "Cache-Control": "private, max-age=86400" },
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" fill="gray"/></svg>' });
  });
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  const list = page.getByRole("list", { name: "Listings" });
  await expect(list).toBeVisible();
  // Index 50 is well beyond the mounted viewport and its five overscan rows.
  await expect.poll(() => requested.has(50)).toBe(true);
  expect(await list.getByRole("listitem").count()).toBeLessThan(35);
  expect(requested.has(119)).toBe(false);
  await page.screenshot({ path: info.outputPath("thumbnail-prefetch.png") });
});
