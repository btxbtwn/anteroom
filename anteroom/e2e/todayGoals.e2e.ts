import type { AttentionItem, HomeSummaryResponse } from "../src/api/home";
import { expect, test } from "./fixtures";

test("Today refreshes after a saved goal deletion without a dashboard snapshot", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Needs attention/ })).toBeVisible({ timeout: 5000 });
  const firstSection = page.getByRole("heading", { level: 2 }).first();
  await expect(firstSection).toHaveText(/Needs attention/);
  await expect(page.locator("h1").filter({ hasText: "Today" })).toHaveClass(/sr-only/);
  await page.getByRole("button", { name: "Add goal" }).click();
  await page.getByLabel("Goal target").fill("150");
  await page.getByLabel("Goal label").fill("Repair test goal");
  await page.getByRole("button", { name: "Save goal", exact: true }).click();
  const remove = page.getByRole("button", { name: "Delete Repair test goal" });
  await expect(remove).toBeVisible();

  const failedDelete = "**/api/home/goals/*";
  await page.route(failedDelete, route => route.fulfill({ status: 500 }));
  const dialog = page.getByRole("alertdialog");
  await remove.click();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(remove).toBeVisible();
  await remove.click();
  await dialog.getByRole("button", { name: "Delete goal" }).click();
  await expect(page.getByText("Could not delete goal", { exact: true })).toBeVisible();
  await expect(remove).toBeEnabled();
  await page.unroute(failedDelete);

  await page.route(failedDelete, async (route) => {
    const response = await route.fetch();
    expect(response.ok()).toBe(true);
    await route.fulfill({ response, json: { ok: true } });
  });
  await remove.click();
  await dialog.getByRole("button", { name: "Delete goal" }).click();
  await expect(remove).toHaveCount(0);
});

test("failed attention actions show an error and restore the alert", async ({ page }, info) => {
  const alert: AttentionItem = {
    key: "still-listed:test",
    kind: "still_listed",
    priority: "high",
    title: "Sold coat",
    detail: "Still listed on another marketplace.",
    action_label: "Open listing",
    target: { view: "listings" },
  };
  const snoozed = { ...alert, key: "still-listed:snoozed", title: "Sold boots", reappears_in_days: 4 };
  await page.route(url => url.pathname === "/api/home", async route => {
    const response = await route.fetch();
    const summary: HomeSummaryResponse = await response.json();
    await route.fulfill({ response, json: {
      ...summary, needs_attention: [alert], dismissed: [snoozed], resolved: [],
    } });
  });
  await page.route("**/api/home/attention/*", route => route.fulfill({ status: 500 }));
  await page.goto("/");
  const resolve = page.getByRole("button", { name: "Resolve: Sold coat" });
  await expect(resolve).toBeVisible();
  await page.screenshot({ path: info.outputPath("today.png"), fullPage: true });
  await resolve.click();
  await expect(page.getByText("Could not resolve alert", { exact: true })).toBeVisible();
  await expect(resolve).toBeVisible();

  const snooze = page.getByRole("button", { name: "Snooze Sold coat for 7 days" });
  await snooze.click();
  await expect(page.getByText("Could not snooze alert", { exact: true })).toBeVisible();
  await expect(snooze).toBeVisible();

  await page.getByRole("tab", { name: "Snoozed" }).click();
  const restore = page.getByRole("button", { name: "Restore Sold boots" });
  await restore.click();
  await expect(page.getByText("Could not restore alert", { exact: true })).toBeVisible();
  await expect(restore).toBeVisible();
});
