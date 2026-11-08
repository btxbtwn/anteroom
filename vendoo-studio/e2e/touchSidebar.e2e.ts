import { createListing, expect, test } from "./fixtures";

// A landscape iPad: wider than the phone layout, and a touch screen with no hover.
test.use({ viewport: { width: 1180, height: 820 }, hasTouch: true });

test("a tablet can delete a draft from the sidebar", async ({ page, request }, info) => {
  test.skip(info.project.name !== "desktop", "Runs once, at tablet size");
  const title = `E2E tablet delete ${Date.now()}`;
  await createListing(request, title);
  await page.goto("/");
  const listings = page.getByRole("list", { name: "Listings" });
  await expect(listings.getByRole("link", { name: title, exact: true })).toBeVisible();

  await page.getByRole("button", { name: `Actions for ${title}` }).tap();
  await page.getByRole("menuitem", { name: "Delete" }).tap();
  await page.getByRole("button", { name: "Delete", exact: true }).tap();
  await expect(listings.getByRole("link", { name: title, exact: true })).toHaveCount(0);
});
