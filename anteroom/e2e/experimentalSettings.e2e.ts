import { expect, test } from "./fixtures";

test.beforeEach(async ({ page }) => {
  // The offline sandbox blocks the Mac updater; keep its unrelated polls out of these settings checks.
  await page.route(/\/api\/updates(?:\/progress)?$/, route => route.fulfill({ json: { available: false, status: "idle" } }));
});

test("Experimental preserves saved settings and omits the disabled hosted browser placeholder", async ({ page, request, phone }, info) => {
  const original = await (await request.get("/api/settings/ui")).json();
  const writes: string[] = [];
  page.on("request", request => {
    if (request.url().endsWith("/api/settings/ui") && request.method() === "PUT") writes.push(request.postData() ?? "");
  });

  try {
    for (const theme of ["dark", "light"]) {
      expect((await request.put("/api/settings/ui", { data: { theme } })).ok()).toBe(true);
      const saved = await (await request.get("/api/settings/ui")).json();
      await page.goto("/");
      if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      const sections = page.getByRole("navigation", { name: "Settings sections" });
      await sections.getByRole("button", { name: "General", exact: true }).click();
      await expect(page.getByRole("switch", { name: "Use Vendoo" })).toHaveCount(0);
      if (phone) await page.getByRole("button", { name: "Settings", exact: true }).click();
      await sections.getByRole("button", { name: "Experimental", exact: true }).click();
      const vendoo = page.getByRole("switch", { name: "Use Vendoo" });
      await expect(vendoo).toBeEnabled();
      await expect(vendoo).toHaveAttribute("aria-checked", String(saved.listing_mode === "vendoo"));
      await expect(page.getByRole("switch", { name: "Hosted marketplace browser" })).toHaveCount(0);
      await expect(page.getByText("Coming soon", { exact: true })).toHaveCount(0);
      expect(await (await request.get("/api/settings/ui")).json()).toEqual(saved);
      await page.screenshot({ path: info.outputPath(`experimental-${theme}.png`) });
    }
    if (phone) await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("combobox", { name: "Search settings" }).fill("hosted marketplace browser");
    await expect(page.getByRole("option", { name: "Hosted marketplace browser Experimental" })).toHaveCount(0);
    expect(writes).toEqual([]);
  } finally {
    await page.goto("about:blank");
    expect((await request.put("/api/settings/ui", { data: { theme: original.theme } })).ok()).toBe(true);
  }
});
