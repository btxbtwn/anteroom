import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;
const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2L8AAAAASUVORK5CYII=", "base64");

// The sandbox has no Chrome extension, so the web app explains what Save draft needs.
test("Poshmark, Mercari and Depop offer only Save draft, and ask for Chrome first", async ({ page, request }, info) => {
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "alice@sandbox.test", password: "sandbox" } })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const runtime = await (await request.get(`${api}/api/runtime`, { headers })).json();
  expect(runtime.draft_marketplaces).toEqual(["poshmark", "mercari", "depop"]);
  const title = `Chrome draft ${info.project.name} ${Date.now()}`;
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: { title } })).json();
  expect((await request.post(`${api}/api/drafts/${draft.id}/photos`, { headers, data: pixel })).status()).toBe(201);
  expect((await request.patch(`${api}/api/drafts/${draft.id}`, { headers, data: {
    title, notes: "", expected_updated_at: draft.updated_at, fields: { price: 40, brand: "Pendleton", condition: "Good", description: "Warm wool coat." },
  } })).status()).toBe(200);
  // Listing on these marketplaces stays on the Mac.
  expect((await request.post(`${api}/api/conversations/${draft.id}/native-marketplaces/poshmark/list`, { headers, data: { revision_id: "x", confirm: true } })).status()).toBe(403);

  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (info.project.name === "phone") await page.getByRole("button", { name: "Inventory", exact: true }).click();
  await page.getByRole("list", { name: "Listings" }).getByRole("button", { name: title, exact: true }).click();
  const rail = page.getByRole("navigation", { name: "Marketplaces" });
  for (const name of ["Poshmark", "Mercari", "Depop"]) {
    await rail.getByRole("listitem", { name }).getByRole("button").first().click();
    const pane = page.locator(`[data-marketplace-pane="${name.toLowerCase()}"]`);
    await expect(pane.getByText("Connect Chrome to save drafts.")).toBeVisible();
    await expect(pane.getByRole("button", { name: "Save draft" })).toHaveCount(0);
    await expect(pane.getByRole("button", { name: "List…" })).toHaveCount(0);
    // Let this form's reads finish before opening the next one.
    await page.waitForLoadState("networkidle");
  }
});
