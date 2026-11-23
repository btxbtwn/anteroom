import { expect, test } from "./fixtures";

/** The page links down the left edge of the desktop window (NavRail). */
test.describe("navigation rail", () => {
  test.skip(({ phone }) => phone, "Phones navigate from the drawer");

  test("shows names only while a mouse rests on it, and stays when the listings are hidden", async ({ page }) => {
    await page.goto("/");
    const pages = page.getByRole("navigation", { name: "Pages" });
    const today = pages.getByRole("button", { name: "Today", exact: true });
    const label = today.getByText("Today", { exact: true });
    await expect(label).toHaveCSS("opacity", "0");

    await today.hover();
    await expect(label).toHaveCSS("opacity", "1");
    // Laid over the listings, not pushing them aside.
    const listings = page.getByRole("complementary", { name: "Listings" });
    const listingsLeft = (await listings.boundingBox())!.x;
    expect(listingsLeft).toBeLessThan(100);

    await page.mouse.move(900, 450);
    await expect(label).toHaveCSS("opacity", "0");

    await page.getByRole("button", { name: "Hide listings" }).click();
    await expect(listings).toBeHidden();
    await today.click();
    await expect(today).toHaveAttribute("aria-current", "page");
  });

  test("the pin keeps it open beside the listings across reloads", async ({ page }) => {
    await page.goto("/");
    const listings = page.getByRole("complementary", { name: "Listings" });
    const before = (await listings.boundingBox())!.x;
    await page.getByRole("button", { name: "Keep navigation open" }).click();
    await page.mouse.move(900, 450);
    await expect.poll(async () => (await listings.boundingBox())!.x).toBeGreaterThan(before + 100);

    await page.reload();
    const label = page.getByRole("navigation", { name: "Pages" }).getByText("Analytics", { exact: true });
    await expect(label).toHaveCSS("opacity", "1");
    await page.getByRole("button", { name: "Let navigation narrow" }).click();
    await page.mouse.move(900, 450);
    await expect(label).toHaveCSS("opacity", "0");
  });
});
