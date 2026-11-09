import { test, expect, openListing, titleBox } from "./fixtures";

test("refresh keeps the current listing open", async ({ page, phone, createConversation }) => {
  const title = `Refresh listing ${Date.now()}`;
  const response = await createConversation({ title, notes: "" });
  const { id } = await response.json();
  await openListing(page, phone, title);
  await expect(page).toHaveURL(new RegExp(`listing=${id}`));
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`listing=${id}`));
  await expect(await titleBox(page, phone)).toBeVisible();
});
