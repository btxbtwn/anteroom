import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;
const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2L8AAAAASUVORK5CYII=", "base64");

test("a signed-in seller opens a hosted draft and other sellers cannot see its photos", async ({ page, request }, info) => {
  const login = await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "alice@sandbox.test", password: "sandbox" } });
  const alice = await login.json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const title = `Hosted sandbox ${info.project.name} ${Date.now()}`;
  const created = await request.post(`${api}/api/drafts`, { headers, data: { title } });
  expect(created.status()).toBe(201);
  const draft = await created.json();
  const uploaded = await request.post(`${api}/api/drafts/${draft.id}/photos`, { headers, data: pixel });
  expect(uploaded.status()).toBe(201);
  const photo = await uploaded.json();
  expect((await request.get(`${api}/api/drafts/${draft.id}/photos/${photo.id}`, { headers })).status()).toBe(200);
  const bob = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "bob@sandbox.test", password: "sandbox" } })).json();
  expect((await request.get(`${api}/api/drafts/${draft.id}`, { headers: { Authorization: `Bearer ${bob.access_token}` } })).status()).toBe(404);
  expect((await request.get(`${api}/api/drafts/${draft.id}/photos/${photo.id}`, { headers: { Authorization: `Bearer ${bob.access_token}` } })).status()).toBe(404);
  expect((await request.get(`${api}/api/drafts`)).status()).toBe(401);
  expect((await request.post(`${api}/api/conversations/${draft.id}/vendoo-api/list`, { headers, data: { marketplaces: ["ebay"], confirm: true } })).status()).toBe(403);
  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "Inventory", exact: true }).click();
  }
  await page.getByRole("list", { name: "Listings" }).getByRole("button", { name: title, exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
});

test("generation uses the hosted draft worker and the fake provider", async ({ request }, info) => {
  test.skip(info.project.name === "phone", "The same hosted worker serves both layouts");
  test.setTimeout(180_000);
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "alice@sandbox.test", password: "sandbox" } })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: { title: "Hosted generation" } })).json();
  expect((await request.post(`${api}/api/drafts/${draft.id}/photos`, { headers, data: pixel })).status()).toBe(201);
  const generated = await request.post(`${api}/api/conversations/${draft.id}/generate`, { headers, timeout: 150_000 });
  expect(generated.status()).toBe(200);
  const stream = await generated.text();
  expect(stream).not.toContain("event: error");
  expect(stream).toContain("data: [DONE]");
  const saved = await (await request.get(`${api}/api/drafts/${draft.id}`, { headers })).json();
  expect(saved.title).toContain("Sandbox Levi's");
});
