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

test("cloud connections can be checked with Chrome closed", async ({ page }, info) => {
  test.setTimeout(90_000);
  if (info.project.name === "desktop") await page.setViewportSize({ width: 1440, height: 900 });
  const cloud = (marketplace: string, state: "signed_in" | "expired", label: string | null = null) => ({
    marketplace, state, mode: "cloud", cloud_connected: state === "signed_in",
    expiring: false, expires_at: null, label, connected_at: "2026-11-24T20:00:00Z",
  });
  const poshmarkCloud = cloud("poshmark", "signed_in", "poshmark-cloud");
  const depopCloud = cloud("depop", "signed_in", "depop-cloud");
  await page.route("**/api/cloud-sessions", route => route.fulfill({
    json: { enabled: ["poshmark", "depop"], sessions: { poshmark: poshmarkCloud, depop: depopCloud } },
  }));
  let finishChromeCheck!: () => void;
  const chromeCheck = new Promise<void>(resolve => { finishChromeCheck = resolve; });
  await page.route("**/api/marketplaces/sessions", async route => {
    await chromeCheck;
    await route.fulfill({ json: { sessions: {}, chrome_sessions: {}, chrome_connected: false } });
  });
  await page.route("**/api/cloud-sessions/poshmark/verify", route => route.fulfill({
    json: { ok: true, session: poshmarkCloud },
  }));
  let rejectDepopTest!: () => void;
  const depopTest = new Promise<void>(resolve => { rejectDepopTest = resolve; });
  await page.route("**/api/cloud-sessions/depop/verify", async route => {
    await depopTest;
    await route.fulfill({ json: {
      ok: false, session: cloud("depop", "expired"), error: "Depop asked to sign in again.",
    } });
  });
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible({ timeout: 20_000 });
  await openConnections(page, info.project.name === "phone");
  const poshmark = page.locator("#chrome-poshmark");
  const poshmarkBrowser = poshmark.getByRole("group", { name: "Chrome", exact: true });
  const poshmarkHosted = poshmark.getByRole("group", { name: "Cloud", exact: true });
  try {
    await expect(poshmarkHosted.getByText("Signed in as poshmark-cloud", { exact: true })).toBeVisible();
  } finally {
    finishChromeCheck();
  }
  await expect(poshmarkBrowser.getByRole("button", { name: "Sign in to Poshmark in Chrome" })).toBeDisabled();
  await expect(poshmarkHosted.getByRole("button", { name: "Sign in to Poshmark in Cloud" })).toBeEnabled();
  await expect(poshmarkBrowser.getByRole("button", { name: "Sign in to Poshmark in Chrome" }))
    .toHaveAccessibleDescription("Keep your sign-in in your browser. Leave Chrome open for listing actions and sales checks.");
  await expect(poshmarkHosted.getByRole("button", { name: "Sign in to Poshmark in Cloud" }))
    .toHaveAccessibleDescription("Anteroom saves an encrypted sign-in so listing actions, sales checks, and enabled automations can run with Chrome closed.");
  await poshmarkHosted.getByRole("button", { name: "Test Poshmark cloud connection" }).click();
  await expect(poshmarkHosted.getByText("Cloud sign-in verified.", { exact: true })).toBeVisible();
  await expect(poshmarkHosted.getByRole("button", { name: "Disconnect Poshmark from Cloud" })).toBeVisible();
  const depop = page.locator("#chrome-depop");
  const depopHosted = depop.getByRole("group", { name: "Cloud", exact: true });
  await expect(depopHosted).toContainText("Signed in as depop-cloud");
  await depopHosted.getByRole("button", { name: "Test Depop cloud connection" }).click();
  await expect(depopHosted.getByText("Testing connection…", { exact: true })).toBeVisible();
  // Keep the follow-up status request in flight. The test reply itself must update the badge.
  let resumeStatus!: () => void;
  const pendingStatus = new Promise<void>(resolve => { resumeStatus = resolve; });
  await page.route("**/api/cloud-sessions", async route => {
    await pendingStatus;
    await route.fulfill({ json: { enabled: ["poshmark", "depop"], sessions: { poshmark: poshmarkCloud, depop: cloud("depop", "expired") } } });
  });
  rejectDepopTest();
  try {
    await expect(depopHosted.getByText("Sign-in expired", { exact: true })).toBeVisible();
    await expect(depopHosted.getByText("Depop asked to sign in again.", { exact: true })).toBeVisible();
  } finally {
    resumeStatus();
  }
  const mercari = page.locator("#chrome-mercari");
  await expect(mercari).toBeVisible();
  const mercariBrowser = mercari.getByRole("group", { name: "Chrome", exact: true });
  await expect(mercariBrowser.getByRole("button", { name: "Sign in to Mercari in Chrome" }))
    .toHaveAccessibleDescription("Keep desktop Chrome open for Mercari listing actions and sales checks. Anteroom Bridge remembers your sign-in after Chrome restarts.");
  await expect(mercari.getByRole("group", { name: "Cloud", exact: true })).toHaveCount(0);
  await depop.scrollIntoViewIfNeeded();
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(colorScheme === "dark");
    await page.screenshot({ path: `e2e/results/hosted/session-cloud-connections-${info.project.name}-${colorScheme}.png` });
  }
});

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
  // Chrome isn't connected in the sandbox: the switch shows off with the reason, and the saved choice is kept.
  const detection = page.getByRole("switch", { name: "Sale detection for Poshmark", exact: true });
  await expect(detection).not.toBeChecked();
  await expect(detection).toBeDisabled();
  await expect(page.locator("#chrome-poshmark")).toContainText("Connect Chrome to detect sales");
  const reloaded = page.waitForResponse(response => response.url().endsWith("/api/settings/sale-detection") && response.request().method() === "GET" && response.ok());
  await page.reload();
  await openConnections(page, phone);
  expect((await (await reloaded).json()).enabled).toEqual(initialDetection.enabled);
  await page.locator("#chrome-poshmark").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `e2e/results/hosted/session-settings-${info.project.name}.png`, fullPage: true });
  expect(macReads).toEqual([]);
  expect(errors).toEqual([]);
});
