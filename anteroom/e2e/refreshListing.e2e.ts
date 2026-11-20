import { test, expect, openListing, titleBox } from "./fixtures";

test("refresh keeps the current listing open", async ({ page, phone, createConversation }) => {
  const title = `Refresh listing ${Date.now()}`;
  const response = await createConversation({ title, notes: "" });
  const { id } = await response.json();
  // Let the initial chat and photo reads finish before replacing the page.
  // WebKit can report same-origin fetches interrupted by reload as access errors.
  const initialReads = ["messages", "photos"].map(part => page.waitForResponse(
    r => new URL(r.url()).pathname === `/api/conversations/${id}/${part}` && r.ok(),
  ).then(r => r.json()));
  await Promise.all([openListing(page, phone, title), ...initialReads]);
  await expect(page).toHaveURL(new RegExp(`listing=${id}`));
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`listing=${id}`));
  await expect(await titleBox(page, phone)).toBeVisible();
});
