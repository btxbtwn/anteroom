import { expect, recordSale, test } from "./fixtures";

// These flows create and amend several sales before exercising the screen.
test.setTimeout(120_000);

test("sales search, completeness and loss filters update records and exports", async ({ page, request, phone, createConversation }, info) => {
  const prefix = `Filter sale ${info.project.name} ${Date.now()}`;
  for (const [suffix, fees] of [["missing", null], ["loss", 2]] as const) {
    const response = await createConversation({ title: `${prefix} ${suffix}`, notes: "" });
    expect(response.ok()).toBe(true);
    await recordSale(request, (await response.json()).id, {
      marketplace: "ebay", item_revenue: "10", item_cost: "20", fees: fees === null ? null : String(fees), shipping_cost: "0",
    });
  }
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else await page.getByRole("button", { name: "Analytics", exact: true }).click();
  // Phones keep search and the other filters behind Filters, so the figures come first.
  if (phone) await page.getByRole("button", { name: "Filters" }).click();
  await page.getByRole("searchbox", { name: "Search sales" }).fill(prefix);
  await expect(page.getByText("1–2 of 2 records · Page 1", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Sales completeness" }).click();
  await page.getByRole("option", { name: "Losses", exact: true }).click();
  await expect(page.getByText("1–1 of 1 records · Page 1", { exact: true })).toBeVisible();
  const panel = page.getByRole("tabpanel", { name: "Sales records", exact: true });
  await expect(panel.getByText(`${prefix} loss`, { exact: true })).toBeVisible();
  await expect(panel.getByText(`${prefix} missing`, { exact: true })).toHaveCount(0);
  const exported = page.waitForRequest(/\/api\/sales-ledger\/export\/sales\.csv\?.*attention=loss/);
  await page.getByRole("button", { name: "Export CSV" }).click();
  await page.getByRole("menuitem", { name: "Export sales CSV" }).click();
  await exported;
  await page.screenshot({ path: info.outputPath("sales-records-dark.png"), fullPage: true });
  await page.evaluate(() => document.documentElement.classList.remove("dark"));
  await page.screenshot({ path: info.outputPath("sales-records-light.png"), fullPage: true });
  await page.getByRole("combobox", { name: "Sales completeness" }).click();
  await page.getByRole("option", { name: "Missing sale data", exact: true }).click();
  await expect(panel.getByText(`${prefix} missing`, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(page.getByRole("searchbox", { name: "Search sales" })).toHaveValue("");
  await page.getByRole("combobox", { name: "Sales period" }).click();
  await page.getByRole("option", { name: "Custom dates", exact: true }).click();
  await expect(page.getByLabel("Sales from (UTC)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(page.getByLabel("Sales from (UTC)", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Sales period" })).toContainText("Last 30 days");
});

test("recorded amounts appear in sales records and missing data marks sold inventory", async ({ page, request, phone, createConversation }, info) => {
  const today = new Date().toISOString().slice(0, 10);
  const title = `E2E recorded sale ${info.project.name} ${Date.now()}`;
  const created = await createConversation({ title, notes: "" });
  expect(created.ok()).toBe(true);
  const { id } = await created.json();
  await recordSale(request, id, { marketplace: "ebay", item_revenue: "40", item_cost: "5", fees: null, shipping_cost: "4" });
  const filters = `start=${today}&end=${today}&conversation_id=${id}`;
  const imported = await (await request.get(`/api/sales-ledger?${filters}`)).json();
  expect(imported.rows).toHaveLength(1);
  expect(imported.rows[0].financials.item_revenue).toBe("40");
  expect(imported.rows[0].missing_sale_data).toEqual(["fees"]);

  // The create API makes drafts; a settled inventory item also has a stored sold status.
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
  const row = page.getByRole("listitem").filter({ has: page.getByRole("link", { name: title, exact: true }) });
  await expect(row.getByText("Missing sale data", { exact: true })).toBeVisible();
  await expect(row.locator('[title="Missing: fees"]')).toBeVisible();
  await page.screenshot({ path: info.outputPath("missing-sale-data.png") });

  // The Filters menu narrows the inventory to sales missing details.
  await page.getByRole("button", { name: "Filters" }).click();
  const missingFilter = page.getByRole("menuitemcheckbox", { name: /Missing sale details/ });
  await missingFilter.click();
  await expect(missingFilter).toBeChecked();
  await page.screenshot({ path: info.outputPath("missing-sale-filter-menu.png") });
  await page.keyboard.press("Escape");
  await expect(row.getByText("Missing sale data", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("missing-sale-filter.png") });
  await page.getByRole("button", { name: /Filters/ }).click();
  await missingFilter.click();
  await page.keyboard.press("Escape");

  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Analytics", exact: true }).click();
  }
  const record = page.getByRole("listitem").filter({ has: page.getByText(title, { exact: true }) });
  await expect(record.getByText("Missing fees", { exact: true })).toBeVisible();
  await record.getByRole("button", { name: "Record amounts" }).click();
  await expect(page.getByLabel("Sale price", { exact: true })).toHaveValue("40");
  await expect(page.getByLabel("Cost of goods", { exact: true })).toHaveValue("5");
  await page.getByLabel("Fees", { exact: true }).fill("8");
  await page.getByRole("button", { name: "Save amounts", exact: true }).click();
  await expect(record.getByText("Profit: $23.00", { exact: true })).toBeVisible();
  const updated = await (await request.get(`/api/conversations/${id}`)).json();
  expect(updated.missing_sale_data).toEqual([]);
  await page.screenshot({ path: info.outputPath("vendoo-sale-amounts.png") });
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory", exact: true }).click();
  }
  await expect(row.getByRole("link", { name: title, exact: true })).toBeVisible();
  await expect(row.getByText("Missing sale data", { exact: true })).toHaveCount(0);
});

test("historical sales can be corrected entirely in Anteroom", async ({ page, request, phone, createConversation }, info) => {
  const title = `E2E historical sale ${info.project.name} ${Date.now()}`;
  const created = await createConversation({ title, notes: "" });
  expect(created.ok()).toBe(true);
  await recordSale(request, (await created.json()).id, {
    marketplace: "poshmark", item_revenue: "40", item_cost: null, fees: "8", shipping_cost: "4", sold_on: null,
  });
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Analytics", exact: true }).click();
  }
  await page.getByRole("tab", { name: "Inventory analytics", exact: true }).click();
  await expect(page.getByRole("region", { name: "Inventory today", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Missing sales data", exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "Sales records", exact: true }).click();
  await page.getByRole("combobox", { name: "Sales period" }).click();
  await page.getByRole("option", { name: "All time", exact: true }).click();
  if (phone) await page.getByRole("button", { name: /^Filters/ }).click();
  await page.getByRole("combobox", { name: "Sales completeness" }).click();
  await page.getByRole("option", { name: "Missing sale data", exact: true }).click();
  await page.screenshot({ path: info.outputPath("sales-records-missing.png"), fullPage: true });
  const row = page.getByRole("listitem").filter({ has: page.getByText(title, { exact: true }) });
  await expect(row.getByText("Missing cost of goods, sale date", { exact: true })).toBeVisible();
  await row.getByRole("button", { name: "Record amounts", exact: true }).click();
  await expect(page.getByLabel("Fees", { exact: true })).toHaveValue("8");
  await expect(page.getByLabel("Sale price", { exact: true })).toHaveValue("40");
  await page.getByLabel("Fees", { exact: true }).fill("9");
  await page.getByLabel("Cost of goods", { exact: true }).fill("5");
  await page.getByLabel("Date", { exact: true }).fill(new Date().toISOString().slice(0, 10));
  await page.screenshot({ path: info.outputPath("imported-vendoo-details.png") });
  await page.getByRole("button", { name: "Save amounts", exact: true }).click();
  await page.getByRole("combobox", { name: "Sales completeness" }).click();
  await page.getByRole("option", { name: "All records", exact: true }).click();
  await expect(row.getByText("Profit: $22.00", { exact: true })).toBeVisible();
  await expect(row.getByText(/^Missing /)).toHaveCount(0);
  await page.reload();
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Analytics", exact: true }).click();
  }
  await expect(row.getByText("Profit: $22.00", { exact: true })).toBeVisible();
});
