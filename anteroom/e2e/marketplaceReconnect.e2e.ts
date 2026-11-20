import { test, expect } from "./fixtures";

test("reconnect timeout and refusal override a saved sign-in and allow a controlled retry", async ({ page, phone }, info) => {
  const saved = { state: "signed_in", expires_at: 2000000000, label: null, expiring: false };
  await page.route("**/api/extension/status", route => route.fulfill({ json: { connected: true, paired: true } }));
  await page.route("**/api/marketplaces/sessions", route => route.fulfill({ json: { sessions: { depop: saved, poshmark: saved, mercari: saved } } }));
  await page.route("**/api/settings/sale-detection", route => route.fulfill({ json: {
    ok: true, checks: { depop: "Chrome did not answer in time. Try again." }, enabled: {}, autodelist: false,
  } }));
  await page.route(/\/api\/updates(?:\/progress)?$/, route => route.fulfill({ json: { available: false, status: "idle" } }));
  let attempts = 0;
  let releaseRetry: (() => void) | undefined;
  await page.route("**/api/marketplaces/depop/connect", async route => {
    attempts += 1;
    if (attempts === 1) return route.fulfill({ status: 409, json: { detail: "Chrome did not answer in time. Try again." } });
    if (attempts === 2) {
      await new Promise<void>(resolve => { releaseRetry = resolve; });
      return route.fulfill({ json: { ok: false, error: "Depop did not answer the sign-in check. Open Depop in Chrome, then try again." } });
    }
    return route.fulfill({ json: { ok: true, signed_in: true, ...saved } });
  });
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Connections", exact: true }).click();
  const depop = page.locator("#chrome-depop");
  await expect(depop).toContainText("Signed in");
  await expect(depop).toContainText("Sale check failed: Chrome did not answer in time. Try again.");
  const retry = depop.getByRole("button", { name: /Sign in again/ });
  await retry.click();
  await expect(depop).toContainText("Sign-in not confirmed");
  await expect(depop.getByRole("alert").filter({ hasText: /^Chrome did not answer/ })).toBeVisible();
  await expect(retry).toBeEnabled();
  await depop.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("depop-reconnect-timeout.png"), fullPage: true });
  await retry.click();
  await expect(retry).toBeDisabled();
  await expect(depop).toContainText("Sign-in not confirmed");
  await expect.poll(() => Boolean(releaseRetry)).toBe(true);
  releaseRetry!();
  await expect(depop.getByRole("alert").filter({ hasText: /^Depop did not answer/ })).toBeVisible();
  await expect(depop).toContainText("Sign-in not confirmed");
  await retry.click();
  await expect(depop).not.toContainText("Sign-in not confirmed");
  await expect(depop.getByRole("alert")).toHaveCount(1);
  await expect(depop.getByRole("alert")).toContainText("Sale check failed:");
  await page.screenshot({ path: info.outputPath("depop-sales-check-after-reconnect.png"), fullPage: true });
  expect(attempts).toBe(3);
});
