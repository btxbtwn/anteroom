import { expect, test } from "./fixtures";

test("operator can see why error emails are paused", async ({ page, phone }, info) => {
  test.setTimeout(120000);
  await page.route("**/api/error-reports/delivery", route => route.fulfill({ json: {
    visible: true, state: "quota_paused", daily_limit: 20, used: 7, pending: 12,
    resume_at: 1794448800, last_error: "SMTP 550: relay quota exceeded", last_success_at: 1794362400,
  } }));
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "General", exact: true }).click();
  const close = page.getByRole("button", { name: "Close navigation" });
  if (phone && await close.isVisible()) await close.click();
  await expect(page.getByText("Paused by Google’s relay quota", { exact: true })).toBeVisible({ timeout: 60000 });
  await expect(page.getByText("7 of 20 email attempts in the last 24 hours; 12 reports pending.", { exact: true })).toBeVisible();
  await expect(page.getByText("SMTP 550: relay quota exceeded", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("email-delivery.png"), fullPage: true });
});
