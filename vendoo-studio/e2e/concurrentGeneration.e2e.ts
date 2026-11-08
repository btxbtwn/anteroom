import { createListing, expect, test, titleBox } from "./fixtures";

test("another batch and inventory selection stay available while generation waits", async ({ page, phone, request }, info) => {
  const suffix = `${info.project.name} ${Date.now()}`;
  const titles = [`E2E concurrent first ${suffix}`, `E2E concurrent second ${suffix}`, `E2E editable ${suffix}`];
  const ids = await Promise.all(titles.map((title) => createListing(request, title)));
  let finish!: () => void;
  const held = new Promise<void>((resolve) => { finish = resolve; });
  const started = new Set<string>();
  await page.route("**/api/conversations/*/generate", async (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3]!;
    started.add(id);
    await held;
    await route.fulfill({ contentType: "text/event-stream", body: 'data: {"title":"Saved test draft"}\n\ndata: [DONE]\n\n' });
  });
  try {
    await page.goto("/");
    await page.evaluate(() => {
      const durations: number[] = [];
      Object.defineProperty(window, "concurrencyInputDurations", { value: durations, configurable: true });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) durations.push(entry.duration);
      }).observe({ type: "event", buffered: true, durationThreshold: 16 });
    });
    if (phone) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    }
    for (let index = 0; index < 2; index++) {
      await page.getByRole("button", { name: "Select listings", exact: true }).click();
      await page.getByRole("list", { name: "Listings" }).getByRole("checkbox", { name: `Select ${titles[index]}`, exact: true }).click();
      await page.getByRole("region", { name: "Bulk listing actions" }).getByRole("button", { name: "More", exact: true }).click();
      await page.getByRole("menuitem", { name: "Regenerate…", exact: true }).click();
      await page.getByRole("button", { name: "Rewrite 1", exact: true }).click();
      await expect.poll(() => started.has(ids[index]!)).toBe(true);
    }
    expect(started.size).toBe(2);
    await expect(page.getByRole("button", { name: "Select listings", exact: true })).toBeEnabled();
    await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: titles[2], exact: true }).click();
    const field = await titleBox(page, phone);
    await field.fill(`Edited during concurrent work ${suffix}`);
    await expect(field).toHaveValue(`Edited during concurrent work ${suffix}`);
    const durations = await page.evaluate(() => (
      window as unknown as { concurrencyInputDurations: number[] }
    ).concurrencyInputDurations.slice().sort((a, b) => a - b));
    await info.attach("interaction-latency.json", { contentType: "application/json", body: JSON.stringify({
      overlappingBatches: started.size, observedInteractions: durations.length,
      p50Ms: durations[Math.floor(durations.length * .5)] ?? null,
      p95Ms: durations[Math.min(durations.length - 1, Math.floor(durations.length * .95))] ?? null,
      note: "Browser event timings at or above 16ms; shorter interactions are omitted. Generation responses are held open.",
    }, null, 2) });
    await info.attach("responsive-during-generation.png", { contentType: "image/png", body: await page.screenshot() });
  } finally {
    finish();
    await page.unrouteAll({ behavior: "wait" });
    for (const id of ids) await request.delete(`/api/conversations/${id}`);
  }
});
