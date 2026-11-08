import { createListing, expect, openListing, openForm, test, titleBox } from "./fixtures";

const SAMPLES = [
  "Levi's M Blue Cotton Crew Neck T-Shirt",
  "Madewell 28 Black High Rise Skinny Jeans",
  "Free People S Pink Floral Midi Dress",
];

test("the inventory shows the sample listings", async ({ page, phone }) => {
  await page.goto("/");
  if (phone) {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  const listings = page.getByRole("list", { name: "Listings" });
  for (const title of SAMPLES) {
    await expect(listings.getByRole("link", { name: title, exact: true })).toBeVisible();
  }
});

test("a saved listing opens in the editor", async ({ page, phone }) => {
  await openListing(page, phone, SAMPLES[0]);
  await expect(await titleBox(page, phone)).toHaveValue(SAMPLES[0]);
});

test("generating from photos fills the listing", async ({ page, phone, request }, info) => {
  // Generation runs every real pass (evidence, category, repair, required fields); only the model is fake.
  test.setTimeout(150_000);
  const title = `E2E generate ${info.project.name} ${Date.now()}`;
  await createListing(request, title);
  await openListing(page, phone, title);
  await page.getByRole("button", { name: "Generate listing", exact: true }).click();
  await expect(await titleBox(page, phone)).toHaveValue(/Sandbox Levi's M Blue/, { timeout: 120_000 });
});

test("the sandbox refuses to publish or delist", async ({ request }, info) => {
  const id = await createListing(request, `E2E publish ${info.project.name} ${Date.now()}`);
  for (const path of [
    `/api/conversations/${id}/marketplace-listings/delist`,
    `/api/conversations/${id}/native-marketplaces/ebay/list`,
  ]) {
    const response = await request.post(path, { data: { marketplaces: ["ebay"], confirm: true } });
    expect(response.status(), path).toBe(403);
  }
});

test("a marketplace form can be filled in without generating", async ({ page, phone, request }, info) => {
  const title = `E2E by hand ${info.project.name} ${Date.now()}`;
  const id = await createListing(request, title);
  // Leave the inventory as the other tests expect it: the phone list shows only its first page.
  try {
    await openListing(page, phone, title);
    await expect(await titleBox(page, phone)).toBeVisible();
    await openForm(page, phone, "Poshmark");
    const form = page.getByRole("group", { name: "Poshmark listing fields" });
    if (phone) {
      // Like the iPhone app, a phone gives the form the full width: no rail beside it.
      const box = await form.boundingBox();
      expect(box!.width).toBeGreaterThan(page.viewportSize()!.width * 0.85);
      await expect(page.getByRole("navigation", { name: "Marketplaces" }).getByRole("button", { name: /^Form, Poshmark/ })).toBeVisible();
    }
    const field = form.getByRole("textbox", { name: "Title *" });
    await field.fill("Hand typed tee");
    await field.blur();
    // The first edit saves the draft's first version; no generation is needed.
    await expect.poll(async () => {
      const saved = await (await request.get(`/api/conversations/${id}/listing`)).json();
      return saved.current_revision_id ? JSON.stringify(saved.listing) : "";
    }, { timeout: 15_000 }).toContain("Hand typed tee");
    await expect(form.getByRole("textbox", { name: "Title *" })).toHaveValue("Hand typed tee");
  } finally {
    await request.delete(`/api/conversations/${id}`);
  }
});

test("the marketplace rail stays put when dragged", async ({ page, phone }) => {
  test.skip(phone, "a phone picks the form from a menu, not a rail");
  // An iPad in landscape, where Safari let the rail be pulled sideways and past its ends.
  await page.setViewportSize({ width: 1366, height: 1024 });
  await openListing(page, phone, SAMPLES[0]);
  const rail = page.getByRole("navigation", { name: "Marketplaces" });
  await expect(rail).toBeVisible();
  const box = await rail.evaluate((el) => {
    const style = getComputedStyle(el);
    return { overflowX: style.overflowX, overscroll: style.overscrollBehaviorY,
      wider: el.scrollWidth > el.clientWidth };
  });
  // Nothing to scroll sideways, and no rubber band past the top or bottom.
  expect(box).toEqual({ overflowX: "hidden", overscroll: "none", wider: false });
});
