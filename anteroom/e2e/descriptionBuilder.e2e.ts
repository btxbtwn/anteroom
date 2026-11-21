import type { Locator, Page } from "@playwright/test";
import { expect, test } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.route(/\/api\/updates(?:\/progress)?$/, (route) =>
    route.fulfill({ json: { available: false, status: "idle" } }),
  );
});

async function openListingFormulas(page: Page, phone: boolean) {
  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const sections = page.getByRole("navigation", { name: "Settings sections" });
  await sections.getByRole("button", { name: "Listings", exact: true }).click();
  const formulas = page.locator("#listing-formulas");
  await formulas.scrollIntoViewIfNeeded();
  await expect(formulas.locator('[data-slot="description-active-zone"]')).toBeVisible({
    timeout: 15_000,
  });
  return formulas;
}

async function activeOrder(formulas: Locator): Promise<string[]> {
  return formulas
    .locator('[data-slot="description-active-block"]')
    .evaluateAll((rows) => rows.map((row) => row.getAttribute("data-block-id") || ""));
}

async function mouseDrag(page: Page, from: Locator, to: Locator, targetYFraction = 0.25) {
  await from.scrollIntoViewIfNeeded();
  const start = (await from.boundingBox())!;
  const end = (await to.boundingBox())!;
  const x0 = start.x + start.width / 2;
  const y0 = start.y + start.height / 2;
  const x1 = end.x + end.width / 2;
  const y1 = end.y + end.height * targetYFraction;
  await page.mouse.move(x0, y0);
  await page.mouse.down();
  for (let step = 1; step <= 12; step += 1) {
    await page.mouse.move(x0 + ((x1 - x0) * step) / 12, y0 + ((y1 - y0) * step) / 12);
  }
  await page.mouse.up();
}

test("description maker supports dragging, dropping, reordering, and saving attributes", async ({
  page,
  request,
  phone,
}) => {
  test.setTimeout(120_000);
  const original = await (await request.get("/api/settings/formulas")).json();
  try {
    expect(
      (await request.put("/api/settings/formulas", { data: { title: "", description: "" } })).ok(),
    ).toBe(true);

    const formulas = await openListingFormulas(page, phone);
    await expect.poll(() => activeOrder(formulas)).toEqual(["vibe", "flaws", "measurements"]);

    if (phone) {
      // Every available attribute row and its + button must stay inside the phone viewport.
      const addItemButton = formulas.getByRole("button", { name: "Add Brand, item, color, and size" });
      await addItemButton.scrollIntoViewIfNeeded();
      const box = (await addItemButton.boundingBox())!;
      const viewport = page.viewportSize()!;
      expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);

      // Single-pointer accessible controls work cleanly on phones without dragging.
      await formulas.getByRole("button", { name: "Add Material" }).click();
      await expect.poll(() => activeOrder(formulas)).toEqual([
        "vibe",
        "flaws",
        "measurements",
        "material",
      ]);
      await formulas.getByRole("button", { name: "Move Measurements up" }).click();
      await formulas.getByRole("button", { name: "Move Measurements up" }).click();
      await expect.poll(() => activeOrder(formulas)).toEqual([
        "measurements",
        "vibe",
        "flaws",
        "material",
      ]);
      await formulas.getByRole("button", { name: "Remove Flaws" }).click();
      await expect.poll(() => activeOrder(formulas)).toEqual([
        "measurements",
        "vibe",
        "material",
      ]);
    } else {
      // Drag Material from Available attributes into the description above Flaws.
      await mouseDrag(
        page,
        formulas.locator('[data-slot="description-available-block"][data-block-id="material"] button[aria-label="Drag Material into description"]'),
        formulas.locator('[data-slot="description-active-block"][data-block-id="flaws"]'),
        0.2,
      );
      await expect.poll(() => activeOrder(formulas)).toEqual([
        "vibe",
        "material",
        "flaws",
        "measurements",
      ]);

      // Drag Measurements to the top of the description.
      await mouseDrag(
        page,
        formulas.locator('[data-slot="description-active-block"][data-block-id="measurements"] button[aria-label="Drag to reorder Measurements"]'),
        formulas.locator('[data-slot="description-active-block"][data-block-id="vibe"]'),
        0.2,
      );
      await expect.poll(() => activeOrder(formulas)).toEqual([
        "measurements",
        "vibe",
        "material",
        "flaws",
      ]);

      // Drag Flaws out to Available attributes to remove it.
      await mouseDrag(
        page,
        formulas.locator('[data-slot="description-active-block"][data-block-id="flaws"] button[aria-label="Drag to reorder Flaws"]'),
        formulas.locator('[data-slot="description-available-zone"]'),
        0.5,
      );
      await expect.poll(() => activeOrder(formulas)).toEqual([
        "measurements",
        "vibe",
        "material",
      ]);
    }

    // Live preview reflects the exact new order before saving.
    const previewText = await formulas
      .locator('[data-slot="description-formula-preview"]')
      .innerText();
    expect(previewText.indexOf("Measurements:")).toBeGreaterThanOrEqual(0);
    expect(previewText.indexOf("Measurements:")).toBeLessThan(
      previewText.indexOf("{trendy vibe/style keyword sentence with period}"),
    );
    expect(
      previewText.indexOf("{trendy vibe/style keyword sentence with period}"),
    ).toBeLessThan(previewText.indexOf("Material:"));
    expect(previewText).not.toContain("Flaws:");

    // Persist and verify after full reload.
    await formulas.getByRole("button", { name: "Save formulas" }).click();
    await expect(formulas.getByRole("button", { name: "Save formulas" })).toBeDisabled({
      timeout: 15_000,
    });

    const reloadedFormulas = await openListingFormulas(page, phone);
    await expect.poll(() => activeOrder(reloadedFormulas)).toEqual([
      "measurements",
      "vibe",
      "material",
    ]);

    if (!phone) {
      // Clearing all sections leaves an empty drop zone that still accepts dragged attributes.
      await reloadedFormulas.getByRole("button", { name: "Clear", exact: true }).click();
      await expect.poll(() => activeOrder(reloadedFormulas)).toEqual([]);
      await mouseDrag(
        page,
        reloadedFormulas.locator('[data-slot="description-available-block"][data-block-id="care"] button[aria-label="Drag Care into description"]'),
        reloadedFormulas.locator('[data-slot="description-active-zone"]'),
        0.5,
      );
      await expect.poll(() => activeOrder(reloadedFormulas)).toEqual(["care"]);
    }

    // Reset restores the built-in default order.
    await reloadedFormulas.getByRole("button", { name: "Reset to defaults" }).click();
    await expect.poll(() => activeOrder(reloadedFormulas)).toEqual([
      "vibe",
      "flaws",
      "measurements",
    ]);
  } finally {
    await request.put("/api/settings/formulas", {
      data: { title: original.title || "", description: original.description || "" },
    });
  }
});
