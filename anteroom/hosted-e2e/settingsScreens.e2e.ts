import { test, expect, type Page } from "@playwright/test";

// Sign-in, Settings and Analytics at desktop (1440×900) and iPhone 15 sizes, in the
// device's light and dark appearance. Screenshots land in e2e/results/hosted/screens-*.

/** Opens the phone sidebar the way the other hosted tests do; in Settings the header's back button opens it. */
async function openPhoneSidebar(page: Page) {
  const navigation = page.getByRole("button", { name: "Open navigation", exact: true });
  const settingsBack = page.getByRole("button", { name: "Settings", exact: true }).first();
  if (await navigation.isVisible()) return navigation.click();
  if (await page.getByRole("navigation", { name: "Settings sections" }).count() === 0 && await settingsBack.isVisible()) return settingsBack.click();
  await page.getByRole("button", { name: /^(Settings|Inventory)$/ }).first().click();
  await page.waitForTimeout(500);
  if (await navigation.isVisible()) await navigation.click();
}

async function closePhoneSidebar(page: Page) {
  const close = page.getByRole("button", { name: "Close navigation", exact: true });
  if (await close.isVisible()) await close.click();
}

async function openSettings(page: Page, section: string, phone: boolean) {
  const button = page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: section, exact: true });
  if (phone && !await button.isVisible()) await openPhoneSidebar(page);
  if (!await button.isVisible()) await page.getByRole("button", { name: "Settings", exact: true }).first().click();
  await button.click();
  if (phone) await closePhoneSidebar(page);
}

/** Leaves Settings (its sidebar shows "Back") and opens a workspace page. */
async function openPage(page: Page, name: string, phone: boolean) {
  if (phone) await openPhoneSidebar(page);
  const back = page.getByRole("button", { name: "Back", exact: true });
  if (await back.isVisible()) await back.click();
  if (phone && !await page.getByRole("button", { name, exact: true }).isVisible()) await openPhoneSidebar(page);
  await page.getByRole("button", { name, exact: true }).click();
  if (phone) await closePhoneSidebar(page);
}

/** A seller's usage as the server reports it: 30 days with three days of spend, or October with one. */
function managedUsage(period: string) {
  const service = { count: 0, usd: 0, input_tokens: 0, output_tokens: 0, items: [] };
  const item = (label: string, count: number, usd: number, tokens = 0) =>
    ({ label, count, usd, input_tokens: Math.round(tokens * 0.8), output_tokens: Math.round(tokens * 0.2) });
  const days = (first: string, spent: Record<string, [number, number]>) => {
    const start = Date.parse(`${first}T00:00:00Z`);
    const length = period === "30d" ? 30 : 31;
    return Array.from({ length }, (_, index) => {
      const date = new Date(start + index * 86_400_000).toISOString().slice(0, 10);
      const [openrouter, photoroom] = spent[date] ?? [0, 0];
      return { date, openrouter, photoroom };
    });
  };
  const months = ["2026-11", "2026-10"];
  if (period === "2026-10") {
    return {
      period: { id: period, start: "2026-10-01T00:00:00+00:00", end: "2026-11-01T00:00:00+00:00" }, months,
      total_usd: 0.1, openrouter: service,
      photoroom: { ...service, count: 1, usd: 0.1, items: [item("Background removed", 1, 0.1)] },
      days: days("2026-10-01", { "2026-10-30": [0, 0.1] }),
    };
  }
  return {
    period: { id: period, start: "2026-10-28T00:00:00+00:00", end: "2026-11-27T00:00:00+00:00" }, months,
    total_usd: 0.5595,
    openrouter: { count: 14, usd: 0.2595, input_tokens: 49_120, output_tokens: 12_280,
      items: [item("google/gemini-2.5-flash", 11, 0.2545, 58_400), item("openai/gpt-5-mini", 3, 0.005, 3_000)] },
    photoroom: { ...service, count: 3, usd: 0.3,
      items: [item("Background removed, shadow", 2, 0.2), item("Ghost mannequin", 1, 0.1)] },
    days: days("2026-10-28", { "2026-10-30": [0, 0.1], "2026-11-20": [0.11, 0.1], "2026-11-26": [0.1495, 0.1] }),
  };
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`${scheme} appearance`, () => {
    test.use({ colorScheme: scheme });

    test("sign-in, Settings and Analytics follow the device and explain blocked controls", async ({ page }, info) => {
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
      await shot("settings-general");
      await openSettings(page, "Usage", phone);
      const plan = page.locator("#subscription-plan");
      await expect(plan.getByText("Free", { exact: true }).first()).toBeVisible();
      await expect(plan.getByText(/\/ 50/)).toBeVisible();
      await expect(plan.getByText(/\/ 10/)).toBeVisible();
      const tiers = page.locator("#subscription-tiers");
      await expect(tiers.getByText("$49.99/mo")).toBeVisible();
      await tiers.getByRole("button", { name: "Annual (2 mo free)", exact: true }).click();
      await expect(tiers.getByText("$499.90/yr")).toBeVisible();
      await tiers.getByRole("button", { name: "Monthly", exact: true }).click();
      const usage = page.locator("#managed-usage");
      const downloadCsv = page.getByRole("button", { name: "Download CSV" });
      await expect(usage.getByText("alice@sandbox.test")).toBeVisible();
      await expect(usage.getByText("$0.00", { exact: true })).toHaveCount(1);
      await expect(usage.getByText("No metered usage in this period.")).toBeVisible();
      await expect(downloadCsv).toBeDisabled();
      await shot("settings-usage-empty");
      // The offline sandbox never spends provider credits. Exercise a refresh
      // with distinct costs, including the fractional cents OpenRouter reports.
      const usagePath = /\/api\/settings\/managed-usage(\.csv)?\?period=/;
      await page.route(usagePath, route => {
        const url = new URL(route.request().url());
        const period = url.searchParams.get("period") ?? "30d";
        if (url.pathname.endsWith(".csv")) {
          return route.fulfill({ contentType: "text/csv", body: `time_utc,service,item,input_tokens,output_tokens,cost_usd\n${period}\n` });
        }
        return route.fulfill({ json: managedUsage(period) });
      });
      await page.getByRole("button", { name: "Refresh usage" }).click();
      const rows = usage.locator("[data-slot=settings-row]");
      const total = rows.filter({ hasText: "Total metered usage" });
      await expect(total.getByText("$0.56", { exact: true })).toBeVisible();
      await expect(total).toContainText("Oct 28 – Nov 26, 2026");
      await expect(total).toContainText("Highest day: Nov 26, $0.25");
      await expect(total.getByRole("img", { name: /^Cost per day, Oct 28 to Nov 26/ })).toBeVisible();
      const ai = rows.filter({ hasText: "OpenRouter" });
      await expect(ai.getByText("14 AI calls · 61.4K tokens")).toBeVisible();
      await expect(ai.getByText("$0.26", { exact: true })).toBeVisible();
      await expect(ai.getByRole("listitem").filter({ hasText: "google/gemini-2.5-flash" })).toContainText("$0.25");
      await expect(ai.getByRole("listitem").filter({ hasText: "openai/gpt-5-mini" })).toContainText("$0.005");
      const edits = rows.filter({ hasText: "Photoroom" });
      await expect(edits.getByText("3 photo edits")).toBeVisible();
      await expect(edits.getByText("$0.30", { exact: true })).toBeVisible();
      await expect(usage.getByText("Days and months are in UTC.")).toBeVisible();
      await shot("settings-usage");
      // The same numbers as a table, for anyone who can't read the bars.
      await total.getByRole("button", { name: "Table", exact: true }).click();
      await expect(total.getByRole("row", { name: "Nov 20 $0.11 $0.10 $0.21" })).toBeVisible();
      await expect(total.getByRole("row")).toHaveCount(4);
      await shot("settings-usage-table");
      // A calendar month covers that month only, and its file is named for it.
      await page.getByRole("combobox", { name: "Usage period" }).click();
      await expect(page.getByRole("option")).toHaveText(["Last 30 days", "November 2026", "October 2026"]);
      await page.getByRole("option", { name: "October 2026" }).click();
      await expect(total).toContainText("Oct 1 – 31, 2026");
      // The table is still showing, so the day's row carries the same figure.
      await expect(total.locator("strong")).toHaveText("$0.10");
      await expect(total.getByRole("row", { name: "Oct 30 $0.00 $0.10 $0.10" })).toBeVisible();
      await expect(rows.filter({ hasText: "OpenRouter" }).getByText("0 AI calls")).toBeVisible();
      const download = page.waitForEvent("download");
      await downloadCsv.click();
      expect((await download).suggestedFilename()).toBe("anteroom-usage-2026-10.csv");
      await page.unroute(usagePath);
      await openSettings(page, "Experimental", phone);
      const vendoo = page.getByRole("switch", { name: "Use Vendoo", exact: true });
      await vendoo.scrollIntoViewIfNeeded();
      await expect(vendoo).toBeVisible();
      await shot("settings-experimental");

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

      await openPage(page, "Analytics", phone);
      await page.getByRole("tab", { name: "Boxes", exact: true }).click();
      await expect(page.getByText("Boxes you bought", { exact: true })).toBeVisible();
      await shot("analytics-boxes");
      expect(errors).toEqual([]);
    });
  });
}
