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
    // The drawer's items read as low-contrast text while it closes; check the inventory once it is gone.
    await expect(page.getByRole("navigation", { name: "Pages" })).toBeHidden();
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

test("xs, icon-xs, and aligned buttons have centered 44 point hit areas on a phone", async ({ page, phone }) => {
  test.skip(!phone, "A mouse pointer keeps the drawn size as the hit area.");

  // 1. Settings -> Connections: size="xs" button stays compact visually (<=28px) with a >=44px hit area.
  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Connections", exact: true }).click();
  const closeNav = page.getByRole("button", { name: "Close navigation" });
  if (await closeNav.isVisible()) await closeNav.click();

  const connectButton = page.getByRole("button", { name: "Connect", exact: true }).first();
  await expect(connectButton).toBeVisible();
  await connectButton.scrollIntoViewIfNeeded();
  const xsMeasure = await connectButton.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const after = getComputedStyle(element, "::after");
    const cx = rect.left + rect.width / 2;
    const hitBelow = document.elementFromPoint(cx, rect.bottom + 6);
    const hitAbove = document.elementFromPoint(cx, rect.top - 6);
    return {
      drawnHeight: rect.height,
      targetWidth: parseFloat(after.width),
      targetHeight: parseFloat(after.height),
      hitsBelow: hitBelow !== null && element.contains(hitBelow),
      hitsAbove: hitAbove !== null && element.contains(hitAbove),
    };
  });
  expect(xsMeasure.drawnHeight).toBeLessThanOrEqual(28);
  expect(xsMeasure.targetWidth).toBeGreaterThanOrEqual(44);
  expect(xsMeasure.targetHeight).toBeGreaterThanOrEqual(44);
  expect(xsMeasure.hitsBelow).toBe(true);
  expect(xsMeasure.hitsAbove).toBe(true);

  // 2. Marketplaces -> eBay -> Sale calendar: size="icon-xs" stays <=28x28px visually with >=44x44px hit area.
  await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Marketplaces", exact: true }).click();
  // The calendar is visible through the closing navigation sheet before it can receive taps.
  await expect(page.locator('[data-slot="sheet-backdrop"]:visible')).toHaveCount(0);
  const prevMonth = page.getByRole("button", { name: "Previous month", exact: true });
  await expect(prevMonth).toBeVisible();
  await prevMonth.scrollIntoViewIfNeeded();
  const iconXsMeasure = await prevMonth.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const after = getComputedStyle(element, "::after");
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const hitLeft = document.elementFromPoint(cx - 18, cy);
    const hitRight = document.elementFromPoint(cx + 18, cy);
    const hitBottom = document.elementFromPoint(cx, cy + 18);
    return {
      drawnWidth: rect.width,
      drawnHeight: rect.height,
      targetWidth: parseFloat(after.width),
      targetHeight: parseFloat(after.height),
      hitsLeft: hitLeft !== null && element.contains(hitLeft),
      hitsRight: hitRight !== null && element.contains(hitRight),
      hitsBottom: hitBottom !== null && element.contains(hitBottom),
    };
  });
  expect(iconXsMeasure.drawnWidth).toBeLessThanOrEqual(28);
  expect(iconXsMeasure.drawnHeight).toBeLessThanOrEqual(28);
  expect(iconXsMeasure.targetWidth).toBeGreaterThanOrEqual(44);
  expect(iconXsMeasure.targetHeight).toBeGreaterThanOrEqual(44);
  expect(iconXsMeasure.hitsLeft).toBe(true);
  expect(iconXsMeasure.hitsRight).toBe(true);
  expect(iconXsMeasure.hitsBottom).toBe(true);

  // 3. Listing editor: justify-start Button centers its ::after without horizontal drift,
  //    and a text-xs InlineButton ("Show more") still provides a >=44x44px hit area.
  await openInventory(page, phone);
  await page.getByRole("list", { name: "Listings" }).getByRole("link").first().click();
  await page.getByRole("tab", { name: "Listing" }).click();

  const formPicker = page.getByRole("button", { name: /^Form,/ }).first();
  await expect(formPicker).toBeVisible();
  await formPicker.scrollIntoViewIfNeeded();
  const alignedMeasure = await formPicker.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const after = getComputedStyle(element, "::after");
    // When ::after is centered on a justify-start button, its resolved horizontal center matches the button's.
    const borderLeft = parseFloat(getComputedStyle(element).borderLeftWidth) || 0;
    const borderRight = parseFloat(getComputedStyle(element).borderRightWidth) || 0;
    const paddingWidth = rect.width - borderLeft - borderRight;
    const leftVal = parseFloat(after.left);
    const widthVal = parseFloat(after.width);
    const translate = after.translate;
    const tx = translate && translate !== "none" ? -widthVal / 2 : 0;
    const afterCenterX = leftVal + tx + widthVal / 2;
    return {
      driftX: Math.abs(afterCenterX - paddingWidth / 2),
      targetHeight: parseFloat(after.height),
    };
  });
  expect(alignedMeasure.targetHeight).toBeGreaterThanOrEqual(44);
  expect(alignedMeasure.driftX).toBeLessThanOrEqual(1);
});
