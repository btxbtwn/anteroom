import AxeBuilder from "@axe-core/playwright";
import { createListing, expect, test } from "./fixtures";

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

test("regenerates only the suggestions selected in the search results", async ({ page, phone, request }, info) => {
  test.setTimeout(90_000);
  const titles = ["Wool coat A", "Wool coat B", "Canvas jacket"];
  const ids: string[] = [];
  try {
    for (const title of titles) ids.push(await createListing(request, title));
    const suggestions = ids.map((id, i) => ({
      conversation_id: id, title: titles[i], kind: "stale_active", age_days: 75,
      reason: "Listed over 60 days.", score: 100 - i, action: "open",
    }));
    await page.route("**/api/suggestions", route => route.fulfill({ json: { suggestions } }));
    const generated: string[] = [];
    page.on("request", req => {
      const match = new URL(req.url()).pathname.match(/^\/api\/conversations\/([^/]+)\/generate$/);
      if (match && req.method() === "POST") generated.push(match[1]!);
    });
    await page.goto("/");
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    }
    await page.getByRole("button", { name: /^Suggestions(?: \d+)?$/ }).click();
    const search = page.getByRole("searchbox", { name: "Search suggestions" });
    await page.getByRole("checkbox", { name: "Select Canvas jacket" }).click({ timeout: 5_000 });
    await search.fill("Wool");
    await expect(page.getByRole("checkbox", { name: "Select Canvas jacket" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Regenerate selected" })).toBeDisabled();
    await page.getByRole("button", { name: "Select all shown" }).click();
    await expect(page.getByText("2 selected", { exact: true })).toBeVisible();
    await page.screenshot({ path: info.outputPath("suggestions-selected.png") });
    await page.getByRole("button", { name: "Regenerate selected" }).click();
    const review = page.getByRole("dialog", { name: "Review 2 listings before rewriting" });
    await expect(review.getByText(titles[0]!, { exact: true })).toBeVisible();
    await expect(review.getByText(titles[1]!, { exact: true })).toBeVisible();
    expect(generated).toEqual([]);
    await review.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByText("2 selected", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Regenerate selected" }).click();
    await review.getByRole("button", { name: "Rewrite 2" }).click();
    await expect(page.getByText("Rewrote 2 listings", { exact: true })).toBeVisible({ timeout: 30_000 });
    expect(generated.sort()).toEqual(ids.slice(0, 2).sort());
  } finally {
    for (const id of ids) await request.delete(`/api/conversations/${id}`);
  }
});
