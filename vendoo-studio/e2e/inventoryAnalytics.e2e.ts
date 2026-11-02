import { expect, test } from "./fixtures";

test("inventory analytics periods and breakdown search remain clear on every screen", async ({ page, request, phone, createConversation }, info) => {
  const created = await createConversation({ title: "Analytics sample sale", notes: JSON.stringify({
    vendooStatus: "sold", vendooSale: { marketplace: "ebay", price: 40, cost: 5, fees: 8, shippingCost: 4, soldAt: new Date().toISOString() },
  }) });
  expect(created.ok()).toBe(true);
  const { id } = await created.json();
  const saved = await request.put(`/api/conversations/${id}/listing`, { data: { listing: { title: "Analytics sample sale", brand: "Acme", price: 40, quantity: 1 }, expected_revision_id: "" } });
  expect(saved.ok()).toBe(true);
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else await page.getByRole("button", { name: "Analytics", exact: true }).click();
  await page.getByRole("tab", { name: "Inventory analytics", exact: true }).click();
  const panel = page.getByRole("tabpanel", { name: "Inventory analytics", exact: true });
  await expect(panel.getByRole("combobox", { name: "Sales period", exact: true })).toContainText("Last 12 months");
  await panel.getByRole("combobox", { name: "Sales period", exact: true }).click();
  await page.getByRole("option", { name: "Custom", exact: true }).click();
  await expect(panel.getByLabel("From", { exact: true })).toBeVisible();
  await panel.getByRole("combobox", { name: "Sales period", exact: true }).click();
  await page.getByRole("option", { name: "All time", exact: true }).click();
  await panel.getByRole("searchbox", { name: "Search breakdowns" }).fill("Acme");
  await expect(panel.getByText("Acme", { exact: true })).toBeVisible();
  await expect(panel.getByText("No matching categories. Try another name or clear the search.", { exact: true })).toBeVisible();
  await panel.getByRole("combobox", { name: "Rank breakdowns by" }).click();
  await page.getByRole("option", { name: "Profit", exact: true }).click();
  await expect(panel.getByRole("combobox", { name: "Rank breakdowns by" })).toContainText("Profit");
  await panel.getByRole("button", { name: "Clear breakdown search" }).click();
  await expect(panel.getByRole("searchbox", { name: "Search breakdowns" })).toHaveValue("");
  await panel.getByRole("link", { name: "Review inventory today" }).click();
  await expect(panel.getByRole("region", { name: "Inventory today", exact: true })).toBeInViewport();
  await page.screenshot({ path: info.outputPath("inventory-health-dark.png") });
  await panel.getByRole("combobox", { name: "Sales period", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("inventory-analytics-dark.png") });
  await page.evaluate(() => document.documentElement.classList.remove("dark"));
  await page.screenshot({ path: info.outputPath("inventory-analytics-light.png") });
});
