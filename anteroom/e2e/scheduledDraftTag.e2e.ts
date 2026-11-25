import { createListing, expect, showStatusTab, test } from "./fixtures";

test.use({ locale: "en-US", timezoneId: "America/Chicago" });

test("draft rows show the scheduled publish time in local time", async ({ page, phone, request }, info) => {
  const title = "Patagonia L Vintage Fleece Jacket Gray";
  const id = await createListing(request, title);
  const draft = `/api/conversations/${id}`;
  try {
    const saved = await request.put(`${draft}/listing`, { data: { expected_revision_id: "", listing: {
      title, description: "Warm heather gray quarter-zip fleece jacket in great pre-owned condition.\n\nFlaws: None\nMeasurements: Chest 22 in, Length 28 in",
      brand: "Patagonia", category_path: "Clothing, Shoes & Accessories > Men > Men's Clothing > Coats, Jackets & Vests", condition: "Good",
      price: 58, quantity: 1, sku: "PAT-001", size: "L", color: "Gray",
      weight_lb: 1, weight_oz: 4, package_dimensions_in: "13x10x3",
    } } });
    expect(saved.ok(), await saved.text()).toBe(true);
    const when = new Date(Date.now() + 3 * 24 * 60 * 60_000);
    when.setUTCHours(1, 30, 0, 0); // In Chicago, this is the previous evening.
    const publishAt = when.toISOString();
    const scheduled = await request.post(`${draft}/schedule`, { data: {
      publish_at: publishAt, marketplaces: ["poshmark"], confirm: true,
    } });
    expect(scheduled.ok(), await scheduled.text()).toBe(true);

    await page.goto("/");
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    }
    await showStatusTab(page, "Draft");
    const list = page.getByRole("list", { name: "Listings" });
    const row = list.getByRole("listitem").filter({ has: page.getByRole("link", { name: title, exact: true }) });
    const date = when.toLocaleDateString("en-US", { timeZone: "America/Chicago", year: "numeric", month: "short", day: "numeric" });
    const time = when.toLocaleTimeString("en-US", { timeZone: "America/Chicago", hour: "numeric", minute: "2-digit" });
    await expect(row.getByText("Scheduled", { exact: true })).toBeVisible();
    await expect(row.locator("time")).toHaveText(`${date} ${time}`);
    await page.screenshot({ path: info.outputPath("scheduled-draft.png") });
    await page.evaluate(() => document.documentElement.classList.toggle("dark"));
    await page.screenshot({ path: info.outputPath("scheduled-draft-other-theme.png") });
    await page.evaluate(() => document.documentElement.classList.toggle("dark"));

    await page.getByRole("button", { name: "Inventory table" }).click();
    await page.getByRole("group", { name: "Listing status" }).getByRole("button", { name: /^Draft\b/ }).click();
    const tableRow = page.getByRole("rowheader").filter({ hasText: title });
    await expect(tableRow.getByText("Scheduled", { exact: true })).toBeVisible();
    await expect(tableRow.locator("time")).toHaveText(`${date} ${time}`);
    if (phone) await page.getByRole("button", { name: "Inventory", exact: true }).click();

    await row.getByRole("link", { name: title, exact: true }).click();
    if (phone) {
      await page.getByRole("tab", { name: "Ant", exact: true }).click();
      await expect(page.getByText("Ask Ant about this listing")).toBeVisible();
    } else {
      await expect(page.getByRole("navigation", { name: "Marketplaces" })).toBeVisible();
    }
    const threadSchedule = page.getByRole("status", { name: "Draft schedule" });
    await expect(threadSchedule.getByText("Scheduled", { exact: true })).toBeVisible();
    await expect(threadSchedule.locator("time")).toHaveText(`${date} ${time}`);
    await page.screenshot({ path: info.outputPath("scheduled-thread.png") });

    if (phone) await page.getByRole("tab", { name: "Listing", exact: true }).click();
    await page.getByRole("button", { name: "Unschedule", exact: true }).click();
    await expect(threadSchedule).toHaveCount(0, { timeout: 20_000 });
    if (phone) await page.getByRole("button", { name: "Inventory", exact: true }).click();
    await expect(row.getByRole("link", { name: title, exact: true })).toBeVisible();
    await expect(row.getByText("Scheduled", { exact: true })).toHaveCount(0);
  } finally {
    const deleted = await request.delete(draft);
    expect(deleted.ok(), await deleted.text()).toBe(true);
  }
});
