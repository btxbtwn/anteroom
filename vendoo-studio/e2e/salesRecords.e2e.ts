import { randomUUID } from "node:crypto";
import { expect, test } from "./fixtures";

test("Vendoo amounts appear in sales records and missing data marks sold inventory", async ({ page, request, phone }, info) => {
  const today = new Date().toISOString().slice(0, 10);
  const title = `E2E Vendoo sale ${info.project.name} ${Date.now()}`;
  const sale = { marketplace: "ebay", price: 40, cost: 5, shippingCost: 4, soldAt: new Date().toISOString() };
  const created = await request.post("/api/conversations", { data: {
    title, notes: JSON.stringify({ vendooStatus: "sold", vendooSale: sale }),
  } });
  expect(created.ok()).toBe(true);
  const { id } = await created.json();
  const filters = `start=${today}&end=${today}&conversation_id=${id}`;
  const imported = await (await request.get(`/api/sales-ledger?${filters}`)).json();
  expect(imported.rows).toHaveLength(1);
  expect(imported.rows[0].financials.item_revenue).toBe("40");
  expect(imported.rows[0].missing_sale_data).toEqual(["fees"]);

  // A seller-confirmed sale gives the inventory its sold status and matches the import.
  const marked = await request.post(`/api/conversations/${id}/marketplace-listings/sold`, { data: {
    marketplace: "ebay", sold_on: today, currency: "USD", request_id: randomUUID().replaceAll("-", ""),
  } });
  expect(marked.ok()).toBe(true);
  // The create API makes drafts; a real Vendoo import also sets the stored status.
  // Supply that status for this fixture while keeping its real API financial data.
  await page.route("**/api/conversations", async route => {
    const response = await route.fetch();
    const rows = await response.json();
    await route.fulfill({ response, json: rows.map((row: { id: string }) => row.id === id ? { ...row, status: "sold" } : row) });
  });
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory", exact: true }).click();
  }
  await page.getByRole("tab", { name: /^Sold/ }).click();
  const row = page.getByRole("listitem").filter({ has: page.getByRole("button", { name: title, exact: true }) });
  await expect(row.getByText("Missing sale data", { exact: true })).toBeVisible();
  await expect(row.locator('[title="Missing: fees"]')).toBeVisible();
  await page.screenshot({ path: info.outputPath("missing-sale-data.png") });

  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Analytics", exact: true }).click();
  }
  const record = page.getByRole("listitem").filter({ has: page.getByText(title, { exact: true }) });
  await expect(record.getByText("Missing: fees", { exact: true })).toBeVisible();
  await record.getByRole("button", { name: "Record amounts" }).click();
  await expect(page.getByLabel("Item revenue total", { exact: true })).toHaveValue("40");
  await expect(page.getByLabel("Item cost total", { exact: true })).toHaveValue("5");
  await page.getByLabel("Net fees", { exact: true }).fill("8");
  await page.getByRole("button", { name: "Save amounts", exact: true }).click();
  await expect(record.getByText("Profit: USD 23", { exact: true })).toBeVisible();
  const updated = await (await request.get(`/api/conversations/${id}`)).json();
  expect(updated.missing_sale_data).toEqual([]);
  await page.screenshot({ path: info.outputPath("vendoo-sale-amounts.png") });
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory", exact: true }).click();
  }
  await expect(row.getByRole("button", { name: title, exact: true })).toBeVisible();
  await expect(row.getByText("Missing sale data", { exact: true })).toHaveCount(0);
});

test("historical imported sales can be corrected entirely in Anteroom", async ({ page, request, phone }, info) => {
  const title = `E2E imported sale ${info.project.name} ${Date.now()}`;
  const created = await request.post("/api/conversations", { data: { title, notes: JSON.stringify({
    vendooStatus: "sold", vendooSale: { marketplace: "poshmark", price: 40, cost: 5, fees: 8,
      shippingCost: 4 },
  }) } });
  expect(created.ok()).toBe(true);
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Analytics", exact: true }).click();
  }
  const row = page.getByRole("listitem").filter({ has: page.getByText(title, { exact: true }) });
  await expect(row.getByText("Profit: USD 23", { exact: true })).toBeVisible();
  await row.getByRole("button", { name: "Record amounts", exact: true }).click();
  await expect(page.getByLabel("Net fees", { exact: true })).toHaveValue("8");
  await expect(page.getByLabel("Item revenue total", { exact: true })).toHaveValue("40");
  await page.getByLabel("Net fees", { exact: true }).fill("9");
  await page.getByLabel("Sale date (UTC)", { exact: true }).fill(new Date().toISOString().slice(0, 10));
  await page.screenshot({ path: info.outputPath("imported-vendoo-details.png") });
  await page.getByRole("button", { name: "Save amounts", exact: true }).click();
  await expect(row.getByText("Profit: USD 22", { exact: true })).toBeVisible();
  await expect(row.getByText("Missing:", { exact: false })).toHaveCount(0);
  await page.reload();
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Analytics", exact: true }).click();
  }
  await expect(row.getByText("Profit: USD 22", { exact: true })).toBeVisible();
});
