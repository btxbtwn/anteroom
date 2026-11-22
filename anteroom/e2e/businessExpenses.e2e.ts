import { expect, test } from "./fixtures";

test("records an expense and exports profit and loss", async ({ page, phone }, info) => {
  const description = `Mailers ${info.project.name} ${crypto.randomUUID()}`;
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Analytics", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Analytics", exact: true }).click();
  }
  await page.getByRole("tab", { name: "Expenses", exact: true }).click();
  const expenses = page.getByRole("tabpanel", { name: "Expenses", exact: true });
  await expenses.getByRole("button", { name: "Add expense" }).click();
  await expenses.getByLabel("Amount", { exact: true }).fill("12.34");
  await expenses.getByLabel("Description", { exact: true }).fill(description);
  await expenses.getByRole("button", { name: "Save expense" }).click();
  await expect(expenses.getByText(description, { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("business-expenses.png") });
  await page.getByRole("tab", { name: "Profit & loss", exact: true }).click();
  const report = page.getByRole("tabpanel", { name: "Profit & loss", exact: true });
  await report.getByRole("combobox", { name: "Accounting mode" }).click();
  await page.getByRole("option", { name: "Cash", exact: true }).click();
  await expect(report.getByText("Inventory Purchases", { exact: true }).first()).toBeVisible();
  const download = page.waitForEvent("download");
  await report.getByRole("button", { name: "Export CSV" }).click();
  expect((await download).suggestedFilename()).toBe("profit-and-loss.csv");
  await page.screenshot({ path: info.outputPath("profit-and-loss.png") });
  await page.getByRole("tab", { name: "Expenses", exact: true }).click();
  const row = expenses.getByText(description, { exact: true }).locator("xpath=../..");
  await row.getByRole("button", { name: "Edit", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await expenses.getByRole("button", { name: "Delete expense" }).click();
  await expect(expenses.getByText(description, { exact: true })).toHaveCount(0);
});
