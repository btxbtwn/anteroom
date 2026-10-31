import { createListing, expect, openListing, test, titleBox } from "./fixtures";

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
    await expect(listings.getByRole("button", { name: title, exact: true })).toBeVisible();
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
    `/api/conversations/${id}/vendoo-api/list`,
    `/api/conversations/${id}/vendoo-api/delist`,
    `/api/conversations/${id}/native-marketplaces/ebay/list`,
  ]) {
    const response = await request.post(path, { data: { marketplaces: ["ebay"], confirm: true } });
    expect(response.status(), path).toBe(403);
  }
});
