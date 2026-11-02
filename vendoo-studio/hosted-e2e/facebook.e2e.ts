import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;

test("on the web, an item already on Facebook is checked through the seller's Chrome", async ({ page, request }, info) => {
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "alice@sandbox.test", password: "sandbox" } })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const title = `Hosted Facebook ${info.project.name} ${Date.now()}`;
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: { title } })).json();
  const marked = await request.post(`${api}/api/conversations/${draft.id}/marketplace-listings/facebook/mark`, {
    headers, data: { state: "listed", url: `https://www.facebook.com/marketplace/item/${Date.now()}/` } });
  expect(marked.status()).toBe(200);

  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (info.project.name === "phone") await page.getByRole("button", { name: "Inventory", exact: true }).click();
  await page.getByRole("list", { name: "Listings" }).getByRole("button", { name: title, exact: true }).click();
  await page.getByRole("listitem", { name: "Facebook" }).getByRole("button", { name: /^Facebook Facebook/ }).click();
  await expect(page.getByText("Anteroom can't delete Facebook listings yet.")).toBeVisible();
  await page.getByRole("button", { name: "Check on Facebook" }).click();
  await expect(page.getByText("Open Anteroom in Chrome on your computer and connect Chrome first.").first()).toBeVisible();
  await page.screenshot({ path: `e2e/results/hosted/facebook-${info.project.name}.png` });
});
