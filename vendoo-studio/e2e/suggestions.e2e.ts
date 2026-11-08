import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "./fixtures";

test("suggestions preserve search, scroll and keyboard focus after reviewing a listing", async ({ page, phone, createConversation }, info) => {
  const response = await createConversation({ title: "Winter wool coat", notes: "" });
  const { id } = await response.json();
  const suggestions = Array.from({ length: 24 }, (_, i) => ({
    conversation_id: i === 20 ? id : `sample-${i}`, title: `Wool coat ${i + 1}`,
    kind: "stale_active", age_days: 75 + i, similar_sold: 3, season_fit: "in",
    reason: "Listed over 60 days. Similar items are selling and this item is in season.", score: 100 - i, action: "open",
  }));
  await page.route("**/api/suggestions", route => route.fulfill({ json: { suggestions } }));
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  const entry = page.getByRole("button", { name: /^Suggestions(?: \d+)?$/ });
  await expect(entry).toContainText("24");
  await page.screenshot({ path: info.outputPath("suggestions-button.png") });
  await entry.click();
  await expect(page.getByRole("heading", { name: "Suggestions", exact: true })).toBeFocused();
  await page.waitForFunction(() => document.getAnimations().every(animation => animation.playState !== "running" || animation.effect?.getTiming().iterations === Infinity));
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter(item => item.impact === "serious" || item.impact === "critical")).toEqual([]);
  const search = page.getByRole("searchbox", { name: "Search suggestions" });
  await search.fill("Wool");
  for (const theme of ["light", "dark"]) {
    await page.evaluate(theme => {
      document.documentElement.classList.toggle("dark", theme === "dark");
      document.documentElement.style.colorScheme = theme;
    }, theme);
    await page.screenshot({ path: info.outputPath(`suggestions-${theme}.png`) });
  }
  const item = page.getByRole("link", { name: /^Wool coat 21(?: |$)/ });
  await item.scrollIntoViewIfNeeded();
  const scroller = page.getByRole("region", { name: "Suggested listings" });
  const scroll = await scroller.evaluate(el => el.scrollTop);
  expect(scroll).toBeGreaterThan(0);
  await item.click();
  await page.getByRole("button", { name: "Back to suggestions", exact: true }).click();
  await expect(search).toHaveValue("Wool");
  await expect(item).toBeFocused();
  await expect.poll(() => scroller.evaluate(el => el.scrollTop)).toBeGreaterThanOrEqual(scroll - 2);
  await search.fill("No matching recommendation");
  await expect(page.getByText("No suggestions match your search.")).toBeVisible();
});
