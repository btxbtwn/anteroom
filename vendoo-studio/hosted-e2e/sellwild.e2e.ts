import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;

test("on the web, the Sellwild form loads and asks for Chrome to read the account choices", async ({ page, request }, info) => {
  const bob = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "bob@sandbox.test", password: "sandbox" } })).json();
  const headers = { Authorization: `Bearer ${bob.access_token}` };
  expect((await request.put(`${api}/api/settings/marketplaces`, { headers, data: { selected: ["ebay", "poshmark", "sellwild"] } })).status()).toBe(200);
  const title = `Hosted Sellwild ${info.project.name} ${Date.now()}`;
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: { title } })).json();
  const saved = await request.put(`${api}/api/conversations/${draft.id}/listing`, { headers, data: {
    listing: { title, description: "Soft cotton tee", price: 20, brand: "Nike" }, expected_revision_id: "" } });
  expect(saved.status()).toBe(200);

  await page.goto("/");
  await page.getByLabel("Email address").fill("bob@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (info.project.name === "phone") await page.getByRole("button", { name: "Inventory", exact: true }).click();
  await page.getByRole("list", { name: "Listings" }).getByRole("button", { name: title, exact: true }).click();
  await page.getByRole("listitem", { name: "Sellwild" }).getByRole("button", { name: "Sellwild Sellwild Not listed" }).click();
  // The product fields load from the shared builder; the account's own choices wait for Chrome.
  await expect(page.getByText("Open Anteroom in Chrome on your computer and connect Chrome first.").first()).toBeVisible();
  const signIn = page.getByRole("link", { name: "Sign in to Sellwild in Chrome" });
  await expect(signIn).toHaveAttribute("href", "https://sellwild.com/");
  await expect(page.getByRole("button", { name: "Reload Sellwild choices" })).toBeVisible();
  await signIn.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `e2e/results/hosted/sellwild-${info.project.name}.png` });
});
