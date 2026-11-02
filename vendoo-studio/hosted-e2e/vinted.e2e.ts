import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;

test("on the web, the Vinted tab reads its choices through the seller's Chrome", async ({ page, request }, info) => {
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "alice@sandbox.test", password: "sandbox" } })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const title = `Hosted Vinted ${info.project.name} ${Date.now()}`;
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: { title } })).json();
  // No Anteroom tab has connected Chrome, so Vinted's choices are not read and nothing waits.
  const categories = await request.get(`${api}/api/conversations/${draft.id}/native-marketplaces/vinted/categories`, { headers });
  expect(categories.status()).toBe(409);
  expect((await categories.json()).detail).toContain("connect Chrome");

  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (info.project.name === "phone") await page.getByRole("button", { name: "Inventory", exact: true }).click();
  await page.getByRole("list", { name: "Listings" }).getByRole("button", { name: title, exact: true }).click();
  await page.getByRole("listitem", { name: "Vinted" }).getByRole("button", { name: "Vinted Vinted Not listed" }).click();
  await expect(page.getByText("Open Anteroom in Chrome on your computer and connect Chrome first.").first()).toBeVisible();
  const signIn = page.getByRole("link", { name: "Sign in to Vinted in Chrome" });
  await expect(signIn).toHaveAttribute("href", "https://www.vinted.com/");
  await signIn.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `e2e/results/hosted/vinted-${info.project.name}.png` });
});
