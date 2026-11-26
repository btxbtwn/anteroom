import { test, expect, type Page } from "@playwright/test";

// Settings → Listings → Help improve listings at desktop and iPhone sizes.
// Screenshots land in e2e/results/hosted/listing-quality-*.

/** Finds the opt-in through Settings search, which opens Listings at that row. */
async function openListingQuality(page: Page, phone: boolean) {
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("combobox", { name: "Search settings" }).fill("help improve listings");
  await page.getByRole("option", { name: "Help improve listings Listings" }).click();
  await expect(page.locator("#listing-quality")).toBeFocused();
}

test("the seller turns listing quality sharing on and off; it starts off", async ({ page }, info) => {
  const phone = info.project.name === "phone";
  if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const saves: unknown[] = [];
  page.on("request", request => {
    if (request.method() === "PUT" && request.url().endsWith("/api/listing-quality/consent")) saves.push(request.postDataJSON());
  });

  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();

  await openListingQuality(page, phone);
  const section = page.locator("#listing-quality");
  const toggle = section.getByRole("switch", { name: "Help improve listings" });
  await expect(toggle).toBeEnabled();
  await section.evaluate(element => element.scrollIntoView({ behavior: "instant", block: "center" }));
  await expect(section).toBeInViewport({ ratio: 0.95 });
  await expect(toggle).not.toBeChecked();
  await expect(section.getByText("Off unless you turn it on.")).toBeVisible();
  await expect(section.getByRole("link", { name: "Privacy policy" })).toHaveAttribute("href", "/privacy#listing-quality");
  expect(saves).toEqual([]);
  await page.screenshot({ path: `e2e/results/hosted/listing-quality-off-${info.project.name}.png` });

  const saved = () => page.waitForResponse(response =>
    response.url().endsWith("/api/listing-quality/consent") && response.request().method() === "PUT");
  const [enabled] = await Promise.all([saved(), toggle.click()]);
  expect(enabled.ok()).toBe(true);
  await expect(toggle).toBeChecked();
  await expect.poll(() => saves).toEqual([{ enabled: true }]);
  await page.screenshot({ path: `e2e/results/hosted/listing-quality-on-${info.project.name}.png` });

  await page.reload();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await openListingQuality(page, phone);
  await expect(toggle).toBeEnabled();
  await expect(toggle).toBeChecked();
  const [disabled] = await Promise.all([saved(), toggle.click()]);
  expect(disabled.ok()).toBe(true);
  await expect(toggle).not.toBeChecked();
  await expect.poll(() => saves).toEqual([{ enabled: true }, { enabled: false }]);
  expect(errors).toEqual([]);
});
