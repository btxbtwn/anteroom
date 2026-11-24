import { expect, showStatusTab, test } from "./fixtures";

test("a seller stars an item, filters to favorites, and unstars it from the editor", async ({ page, phone, request, createConversation }, info) => {
  const stamp = Date.now();
  const starred = `E2E favorite ${stamp} jeans`;
  const plain = `E2E favorite ${stamp} coat`;
  const ids: Record<string, string> = {};
  for (const title of [starred, plain]) ids[title] = (await (await createConversation({ title, notes: "" })).json()).id;

  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await showStatusTab(page, "Draft");
  await page.getByRole("searchbox", { name: /^Search listings/ }).fill(`E2E favorite ${stamp}`);
  const listings = page.getByRole("list", { name: "Listings" });
  const starredLink = listings.getByRole("link", { name: starred, exact: true });
  const plainLink = listings.getByRole("link", { name: plain, exact: true });
  await expect(starredLink).toBeVisible();
  await expect(plainLink).toBeVisible();

  if (phone) {
    await page.getByRole("button", { name: `Actions for ${starred}` }).click();
    await page.getByRole("menuitem", { name: "Add to favorites" }).click();
  } else {
    await starredLink.hover();
    await page.getByRole("button", { name: `Add to favorites: ${starred}` }).click();
  }
  await expect(listings.getByRole("img", { name: "Favorite" })).toHaveCount(1);
  await page.screenshot({ path: info.outputPath("favorites-row.png") });
  await expect.poll(async () => (await (await request.get(`/api/conversations/${ids[starred]}`)).json()).favorite).toBe(true);

  await page.getByRole("button", { name: /^Filters/ }).first().click();
  await page.getByRole("menuitemcheckbox", { name: /^Favorites/ }).click();
  await page.keyboard.press("Escape");
  await expect(starredLink).toBeVisible();
  await expect(plainLink).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("favorites-filter.png") });

  // The star survives a reload and shows, pressed, in the item editor.
  await page.reload();
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await showStatusTab(page, "Draft");
  await page.getByRole("searchbox", { name: /^Search listings/ }).fill(starred);
  await listings.getByRole("link", { name: starred, exact: true }).click();
  // A phone opens the item on its chat; the star sits with the listing's actions.
  if (phone) await page.getByRole("tab", { name: "Listing" }).click();
  const editorStar = page.getByRole("button", { name: `Remove from favorites: ${starred}` });
  await expect(editorStar).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: info.outputPath("favorites-editor.png") });
  await editorStar.click();
  await expect(page.getByRole("button", { name: `Add to favorites: ${starred}` })).toHaveAttribute("aria-pressed", "false");
  await expect.poll(async () => (await (await request.get(`/api/conversations/${ids[starred]}`)).json()).favorite).toBe(false);
});
