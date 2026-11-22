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

test("connects, syncs and disconnects a bank in Expenses", async ({ page, phone }, info) => {
  const root = "/api/business-expenses/plaid";
  const bank = {
    id: "fixture-bank", institution_name: "Example Bank",
    accounts: [{ name: "Business checking", mask: "4567", type: "depository" }],
    has_cursor: true, status: "current", last_synced_at: "2026-11-22T14:30:00Z", last_error: null,
  };
  let connected = false;
  let synced = false;
  await page.route("https://cdn.plaid.com/link/v2/stable/link-initialize.js", (route) =>
    route.fulfill({ contentType: "application/javascript", body: `
      window.Plaid = { create: ({ onSuccess }) => ({
        open: () => onSuccess("fixture-public-token", { institution: { name: "Example Bank" } }),
        destroy: () => {},
      }) };
    ` }),
  );
  await page.route(`**${root}/**`, async (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === `${root}/status`) {
      return route.fulfill({ json: { configured: true, max_connections: 2, connections: connected ? [bank] : [] } });
    }
    if (pathname === `${root}/link-token`) return route.fulfill({ json: { link_token: "fixture-link-token" } });
    if (pathname === `${root}/exchange`) {
      expect(route.request().postDataJSON()).toEqual({ public_token: "fixture-public-token", institution_name: "Example Bank" });
      connected = true;
      return route.fulfill({ status: 201, json: bank });
    }
    if (pathname === `${root}/connections/fixture-bank/sync`) {
      synced = true;
      return route.fulfill({ json: bank });
    }
    if (pathname === `${root}/connections/fixture-bank` && route.request().method() === "DELETE") {
      connected = false;
      return route.fulfill({ status: 204 });
    }
    return route.fallback();
  });
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  const navigation = phone ? page.getByRole("navigation", { name: "Pages" }) : page;
  await navigation.getByRole("button", { name: "Analytics", exact: true }).click();
  await page.getByRole("tab", { name: "Expenses", exact: true }).click();
  const section = page.getByRole("region", { name: "Connected banks and cards" });
  await expect(section.getByText("0/2 connected")).toBeVisible();
  await section.getByRole("button", { name: "Connect bank or card" }).click();
  await expect(section.getByText("Business checking •••• 4567")).toBeVisible();
  await expect(section.getByText("1/2 connected")).toBeVisible();
  await section.getByRole("button", { name: "Sync now" }).click();
  await expect.poll(() => synced).toBe(true);
  await expect(section.getByRole("button", { name: "Sync now" })).toBeEnabled();
  await page.screenshot({ path: info.outputPath("connected-banks.png"), animations: "disabled" });
  await section.getByRole("button", { name: "Disconnect" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText("Previously imported expenses will stay in your books.")).toBeVisible();
  await dialog.getByRole("button", { name: "Disconnect" }).click();
  await expect(section.getByText("0/2 connected")).toBeVisible();
});
