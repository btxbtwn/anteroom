import { expect, test } from "./fixtures";

test("auto-relist recovers from a failed load and only offers supported marketplaces", async ({ page, phone }, info) => {
  let fail = true;
  let release: () => void = () => {};
  const delay = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/automations/auto-relist", async (route) => {
    await delay;
    if (fail) await route.fulfill({ status: 503, json: { detail: "Unavailable" } });
    else await route.continue();
  });
  await page.route("**/api/automations/auto-relist/logs?*", (route) => route.fulfill({
    json: { logs: [{
      id: "relist-1", title: "Sample jacket", marketplace: "poshmark",
      status: "down_needs_relist",
    }] },
  }));
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).first().click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Automations", exact: true }).click();
  const section = page.getByRole("region", { name: "Auto-relist and eBay auto-recreate" });
  try {
    await expect(section.getByRole("status", { name: "Loading Auto-Relist settings" })).toBeVisible();
  } finally {
    release();
  }
  const retry = section.getByRole("button", { name: "Retry" });
  await expect(retry).toBeVisible({ timeout: 15_000 });
  fail = false;
  await retry.click();
  await expect(section.getByRole("switch")).toHaveCount(3);
  await expect(section.getByRole("switch", { name: /Mercari/ })).toHaveCount(0);
  const activity = section.getByRole("button", { name: "Auto-relist log (1)" });
  await activity.click();
  await expect(section.getByText("Old listing removed. Needs relisting.")).toBeVisible();
  await activity.click();

  const poshmark = section.getByRole("switch", { name: /Poshmark/ });
  await expect(poshmark).not.toBeChecked();
  await poshmark.click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation.getByText(/ends any open offers/)).toBeVisible();
  await confirmation.getByRole("button", { name: "Cancel" }).click();
  await expect(poshmark).not.toBeChecked();

  await section.getByRole("button", { name: "Configure" }).first().click();
  const conditions = section.getByRole("combobox", { name: "Conditions" });
  await conditions.press("ArrowDown");
  const firstCondition = page.getByRole("option").first();
  await expect(firstCondition).toBeVisible();
  const condition = (await firstCondition.innerText()).replace(/^○\s*/, "");
  await firstCondition.click();
  await expect(section.getByRole("button", { name: `Remove ${condition}` })).toBeVisible();
  const labels = section.getByRole("combobox", { name: "Labels" });
  await labels.fill("Winter");
  await page.getByRole("option", { name: /Winter/ }).click();
  await expect(section.getByRole("button", { name: "Remove Winter" })).toBeVisible();
  await labels.press("Escape");
  await section.screenshot({ path: info.outputPath("auto-relist-light.png") });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  await section.screenshot({ path: info.outputPath("auto-relist-dark.png") });
});
