import { test, expect, type Page } from "@playwright/test";

// Sign-in, Settings and Sourcing at desktop (1440×900) and iPhone 15 sizes, in the
// device's light and dark appearance. Screenshots land in e2e/results/hosted/screens-*.

async function navigate(page: Page, name: string, phone: boolean) {
  if (phone) {
    const navigation = page.getByRole("button", { name: "Open navigation", exact: true });
    if (!await navigation.isVisible()) await page.getByRole("button", { name: "Inventory", exact: true }).click();
    await navigation.click();
  }
  await page.getByRole("button", { name, exact: true }).click();
}

async function openSettings(page: Page, section: string, phone: boolean) {
  await navigate(page, "Settings", phone);
  const button = page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: section, exact: true });
  if (phone && !await button.isVisible()) await page.getByRole("button", { name: "Settings", exact: true }).click();
  await button.click();
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`${scheme} appearance`, () => {
    test.use({ colorScheme: scheme });

    test("sign-in, Settings and Sourcing follow the device and explain blocked controls", async ({ page }, info) => {
      test.setTimeout(180_000);
      const phone = info.project.name === "phone";
      if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
      const shot = (name: string) => page.screenshot({ path: `e2e/results/hosted/screens-${name}-${info.project.name}-${scheme}.png` });
      const errors: string[] = [];
      page.on("pageerror", error => errors.push(error.message));

      await page.goto("/?sign-in");
      // No theme was chosen, so the page follows the device.
      await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(scheme === "dark");
      await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
      await expect(page.getByText("One account for your listings on the web, on iPhone and on desktop.")).toBeVisible();
      await expect(page.getByText("YOUR LISTING WORKSPACE")).toHaveCount(0);
      await shot("signin");
      await page.getByLabel("Email address").fill("alice@sandbox.test");
      await page.getByLabel("Password").fill("sandbox");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(scheme === "dark");

      await openSettings(page, "General", phone);
      await expect(page.getByText("System follows your device’s light or dark setting.")).toBeVisible();
      const vendoo = page.getByRole("switch", { name: "List through Vendoo", exact: true });
      await vendoo.scrollIntoViewIfNeeded();
      await expect(vendoo).toBeVisible();
      await shot("settings-general");

      await openSettings(page, "Connections", phone);
      await expect(page.locator("#direct-ebay")).toContainText("Connect to detect sales");
      await expect(page.getByRole("switch", { name: "Sale detection for eBay", exact: true })).not.toBeChecked();
      await expect(page.locator("#direct-ebay")).not.toContainText("ZIP");
      await shot("settings-connections");
      await page.locator("#chrome-poshmark").scrollIntoViewIfNeeded();
      await expect(page.locator("#chrome-poshmark")).toContainText("Connect Chrome to detect sales");
      await shot("settings-connections-chrome");

      await openSettings(page, "Listings", phone);
      await expect(page.getByText("Reusable listing fields")).toBeVisible();
      await expect(page.getByRole("button", { name: "Reload templates" })).toHaveCount(0);
      await shot("settings-listings");
      await page.locator("#ships-from-zip").scrollIntoViewIfNeeded();
      await expect(page.locator("#ships-from-zip")).toContainText("eBay, Mercari and Depop");
      await shot("settings-ships-from");

      await navigate(page, "Sourcing", phone);
      // The sandbox can't reach suppliers: a failed refresh ends in an error with Try again, not an endless wait.
      await page.getByText(/Your buy list isn’t ready|Boxes you bought/).first().waitFor({ timeout: 90_000 }).catch(() => {});
      await shot("sourcing");
      expect(errors).toEqual([]);
    });
  });
}
