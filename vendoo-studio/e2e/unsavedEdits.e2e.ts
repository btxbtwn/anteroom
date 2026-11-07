import { createListing, expect, openListing, test, titleBox } from "./fixtures";

// A field saves on blur. Leaving the page (closing the tab, the phone
// backgrounding Safari, an update reload) does not always blur it first.
test("a typed field is saved when the page is hidden before it loses focus", async ({ page, phone, request }, info) => {
  const title = `Unsaved ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await openListing(page, phone, title);
    const field = await titleBox(page, phone);
    await expect(field).toBeVisible();
    await field.fill("Typed then hidden");
    await expect(field).toBeFocused();
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide")));
    await expect.poll(async () => {
      const saved = await (await request.get(`/api/conversations/${id}/listing`)).json();
      return String(saved.listing?.title ?? "");
    }, { timeout: 15_000 }).toBe("Typed then hidden");
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("leaving asks first while an edit is still unsaved", async ({ page, phone, request }, info) => {
  const title = `Leave ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  try {
    await openListing(page, phone, title);
    const field = await titleBox(page, phone);
    await expect(field).toBeVisible();
    // Hold the save open so the edit stays unsaved.
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route(`**/api/conversations/${id}/listing`, async (route) => {
      if (route.request().method() === "GET") return route.continue();
      await held;
      await route.continue();
    });
    await field.fill("Still saving");
    await field.blur();
    const dialog = page.waitForEvent("dialog");
    void page.reload().catch(() => {});
    expect((await dialog).type()).toBe("beforeunload");
    await (await dialog).dismiss();
    release();
    await expect.poll(async () => {
      const saved = await (await request.get(`/api/conversations/${id}/listing`)).json();
      return String(saved.listing?.title ?? "");
    }, { timeout: 15_000 }).toBe("Still saving");
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});
