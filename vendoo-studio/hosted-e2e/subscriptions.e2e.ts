import { test, expect } from "@playwright/test";

for (const connected of [false, true]) test(`desktop subscriptions ${connected ? "connected" : "disconnected"} on web and phone`, async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/settings/provider", async route => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: {
      ...body,
      chatgpt: { signed_in: connected },
      claude: { installed: true, signed_in: connected, install_command: "" },
    } });
  });
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (info.project.name === "phone") await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Providers", exact: true }).click();
  for (const name of ["ChatGPT", "Claude"]) {
    await page.getByRole("group", { name: "Providers", exact: true }).getByRole("button", { name: new RegExp(name) }).click();
    await expect(page.getByText(connected ? `${name} on your computer is connected.` : `Open Anteroom on your computer, sign into your ${name} subscription`, { exact: false })).toBeVisible();
    await expect(page.getByText(connected ? "Keep the computer awake and Anteroom open" : "Subscription credentials stay on your computer.", { exact: false })).toBeVisible();
    if (connected) {
      await page.getByRole("combobox", { name: "Primary listing AI" }).click();
      await expect(page.getByRole("option", { name, exact: true })).toBeEnabled();
      await page.keyboard.press("Escape");
    }
    await page.getByText(connected ? `${name} on your computer is connected.` : `Open Anteroom on your computer, sign into your ${name} subscription`, { exact: false }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `e2e/results/hosted/subscription-${name}-${info.project.name}-${connected}.png` });
  }
  expect(errors).toEqual([]);
});
