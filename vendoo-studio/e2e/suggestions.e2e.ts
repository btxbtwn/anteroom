import { expect, openListing, test } from "./fixtures";

test("suggestions remain reachable with a listing open", async ({ page, phone, createConversation }, info) => {
  const title = `Suggestions ${info.project.name} ${Date.now()}`;
  const response = await createConversation({ title, notes: "" });
  const { id } = await response.json();
  await page.route("**/api/suggestions", route => route.fulfill({ json: { suggestions: [{
    conversation_id: id, title, kind: "stale_active", reason: "Listed 75 days · In season",
    age_days: 75, similar_sold: 2, season_fit: "in", cover_photo_url: null,
  }] } }));
  await openListing(page, phone, title);
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await page.getByRole("button", { name: "Suggestions", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Suggestions" });
  await expect(dialog.getByText("Listed 75 days · In season")).toBeVisible();
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await page.screenshot({ path: info.outputPath(`suggestions-${colorScheme}.png`) });
  }
  await dialog.getByRole("link", { name: new RegExp(title) }).click();
  await expect(dialog).not.toBeVisible();
});
