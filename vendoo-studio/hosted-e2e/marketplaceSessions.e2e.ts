import { test, expect, type Page } from "@playwright/test";

async function navigate(page: Page, name: string, phone: boolean) {
  if (phone) {
    const navigation = page.getByRole("button", { name: "Open navigation", exact: true });
    const back = page.getByRole("button", { name: "Inventory", exact: true });
    // Wait for the screen to settle: the menu, or a back button in front of it.
    await expect(navigation.or(back).first()).toBeVisible();
    if (!await navigation.isVisible()) await back.click();
    await navigation.click();
  }
  await page.getByRole("button", { name, exact: true }).click();
}

async function openConnections(page: Page, phone: boolean) {
  await navigate(page, "Settings", phone);
  const connections = page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Connections", exact: true });
  if (phone && !await connections.isVisible()) await page.getByRole("button", { name: "Settings", exact: true }).click();
  await connections.click();
}

test("hosted Chrome imports, saved-off rules and sale switches are available", async ({ page }, info) => {
  test.setTimeout(90_000);
  const phone = info.project.name === "phone";
  const errors: string[] = [], macReads: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => { if (request.url().endsWith("/api/extension/status")) macReads.push(request.url()); });
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await navigate(page, "Marketplaces", phone);
  for (const name of ["Poshmark", "Mercari", "Depop"]) {
    await page.getByRole("tablist", { name: "Marketplaces" }).getByRole("tab").filter({ hasText: name }).click();
    await expect(page.getByRole("heading", { name: `Import from ${name}`, exact: true })).toBeVisible();
    await expect(page.getByText(/connect Chrome first/).first()).toBeVisible();
    if (name === "Poshmark") {
      await page.getByRole("heading", { name: `Import from ${name}`, exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: `e2e/results/hosted/session-import-${info.project.name}.png` });
    }
  }
  await navigate(page, "Automations", phone);
  await expect(page.getByRole("heading", { name: "Your automations", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New automation", exact: true }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("button", { name: /^Poshmark sharing/ }).click();
  await expect(sheet.getByRole("heading", { name: "New sharing schedule", exact: true })).toBeVisible();
  await expect(sheet.getByText("Saving leaves this automation off. Turn it on from the list when you’re ready. Editing turns it off again.", { exact: true })).toBeVisible();
  const ruleName = `Web sharing ${info.project.name} ${Date.now()}`;
  await sheet.getByRole("textbox", { name: "Name", exact: true }).fill(ruleName);
  const ruleSaved = page.waitForResponse(response => response.url().endsWith("/api/promotion-automations") && response.request().method() === "POST");
  await sheet.getByRole("button", { name: "Save", exact: true }).click();
  expect((await ruleSaved).ok()).toBeTruthy();
  // Saved off: the new row's switch is off until the seller turns it on.
  const toggle = page.getByRole("switch", { name: `Turn on ${ruleName}`, exact: true });
  await expect(toggle).toBeVisible();
  await expect(toggle).not.toBeChecked();
  await page.screenshot({ path: `e2e/results/hosted/session-automations-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "New automation", exact: true }).click();
  await sheet.getByRole("button", { name: /^Offers to likers/ }).click();
  await expect(sheet.getByRole("heading", { name: "New offer rule", exact: true })).toBeVisible();
  await sheet.getByRole("button", { name: "Cancel", exact: true }).click();
  const loaded = page.waitForResponse(response => response.url().endsWith("/api/settings/sale-detection") && response.request().method() === "GET" && response.ok());
  await openConnections(page, phone);
  const initialDetection = await (await loaded).json();
  await expect(page.getByRole("heading", { name: "Accounts", exact: true })).toBeVisible();
  for (const name of ["Poshmark", "Mercari", "Depop"]) {
    await expect(page.getByRole("switch", { name: `Sale detection for ${name}`, exact: true })).toBeVisible();
  }
  const detection = page.getByRole("switch", { name: "Sale detection for Poshmark", exact: true });
  await expect(detection).toBeChecked({ checked: initialDetection.enabled.poshmark !== false });
  const wasChecked = await detection.isChecked();
  const saved = page.waitForResponse(response => response.url().endsWith("/api/settings/sale-detection") && response.request().method() === "PUT");
  await detection.click();
  const response = await saved;
  expect(response.ok()).toBeTruthy();
  expect((await response.json()).enabled.poshmark).toBe(!wasChecked);
  await expect(detection).toBeChecked({ checked: !wasChecked });
  await page.reload();
  await openConnections(page, phone);
  await expect(page.getByRole("switch", { name: "Sale detection for Poshmark", exact: true })).toBeChecked({ checked: !wasChecked });
  await page.locator("#chrome-poshmark").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `e2e/results/hosted/session-settings-${info.project.name}.png`, fullPage: true });
  expect(macReads).toEqual([]);
  expect(errors).toEqual([]);
});
