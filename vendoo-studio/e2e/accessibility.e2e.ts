import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { createListing, expect, openListing, test } from "./fixtures";

// Automated checks find only part of what makes a screen usable without sight
// or a mouse, so these fail on serious and critical findings only.
async function seriousViolations(page: Page) {
  // A drawer still fading out reads as low-contrast text, so let transitions finish first.
  await page.waitForFunction(() => document.getAnimations()
    .every(animation => animation.playState !== "running" || animation.effect?.getTiming().iterations === Infinity));
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations
    .filter(violation => violation.impact === "serious" || violation.impact === "critical")
    .map(violation => ({
      rule: violation.id,
      help: violation.help,
      targets: violation.nodes.map(node => node.target.join(" ")),
    }));
}

async function openInventory(page: Page, phone: boolean) {
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await expect(page.getByRole("list", { name: "Listings" }).getByRole("link").first()).toBeVisible();
}

async function openGeneralSettings(page: Page, phone: boolean) {
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "General", exact: true }).click();
  if (phone) {
    const close = page.getByRole("button", { name: "Close navigation" });
    if (await close.isVisible()) await close.click();
  }
  await expect(page.getByRole("button", { name: "View changelog" })).toBeVisible();
}

test("the inventory has no serious accessibility violations", async ({ page, phone }) => {
  await openInventory(page, phone);
  expect(await seriousViolations(page)).toEqual([]);
});

test("the listing editor has no serious accessibility violations", async ({ page, phone, request }, info) => {
  const title = `Accessibility ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await openListing(page, phone, title);
    await expect(page.getByRole("button", { name: "Generate listing", exact: true })).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("settings have no serious accessibility violations", async ({ page, phone }) => {
  await openGeneralSettings(page, phone);
  expect(await seriousViolations(page)).toEqual([]);
});

test("a small button answers a tap anywhere in a 44 point square on a phone", async ({ page, phone }) => {
  test.skip(!phone, "A mouse pointer keeps the drawn size as the hit area.");
  await openGeneralSettings(page, phone);
  const button = page.getByRole("button", { name: "View changelog" });
  await button.scrollIntoViewIfNeeded();
  const reach = await button.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const target = getComputedStyle(element, "::after");
    // 6 points below the drawn edge, still inside the 44 point square around its centre.
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.bottom + 6);
    return {
      drawnHeight: rect.height,
      targetWidth: parseFloat(target.width),
      targetHeight: parseFloat(target.height),
      hitsButton: hit !== null && element.contains(hit),
    };
  });
  expect(reach.drawnHeight).toBeLessThanOrEqual(28);
  expect(reach.targetWidth).toBeGreaterThanOrEqual(44);
  expect(reach.targetHeight).toBeGreaterThanOrEqual(44);
  expect(reach.hitsButton).toBe(true);
});
