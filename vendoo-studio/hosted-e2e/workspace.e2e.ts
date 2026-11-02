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
  await expect(page.getByRole("button", { name: "More listing actions", exact: true })).toBeVisible();
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

test("a web deployment offers a reload without reloading the seller's tab", async ({ page }) => {
  test.setTimeout(90_000);
  let navigations = 0;
  page.on("request", request => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) navigations++;
  });
  await page.route("**/api/status", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), version: "999.0.0" } });
  });
  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Web update available", { exact: true })).toBeVisible({ timeout: 30_000 });
  expect(navigations).toBe(1);
  const reloaded = page.waitForEvent("request", request => request.isNavigationRequest() && request.frame() === page.mainFrame());
  await page.getByRole("button", { name: "Reload app", exact: true }).click();
  await reloaded;
  expect(navigations).toBe(2);
});

test("generation waits for all four photos and saves the listing from the editor", async ({ page, request }, info) => {
  test.skip(info.project.name === "phone", "Upload state and generation use the same editor in both layouts");
  test.setTimeout(180_000);
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const title = `Four-photo draft ${Date.now()}`;
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: { title } })).json();
  expect((await request.post(`${api}/api/drafts/${draft.id}/photos`, { headers, data: pixel })).status()).toBe(201);
  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("list", { name: "Listings" }).getByRole("button", { name: title, exact: true }).click();

  let release!: () => void;
  const uploading = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/drafts/${draft.id}/photos`, async route => {
    await uploading;
    await route.continue();
  });
  try {
    await page.locator('[data-photo-tray] input[type="file"]').first().setInputFiles(
      [2, 3, 4].map(number => ({ name: `photo-${number}.png`, mimeType: "image/png", buffer: pixel })),
    );
    await expect(page.getByRole("button", { name: "Uploading photos…", exact: true })).toBeDisabled();
  } finally {
    release();
  }
  await expect(page.getByText("4 photos uploaded", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Generate listing", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Title", exact: true }).first()).toHaveValue(/Sandbox Levi's M Blue/, { timeout: 120_000 });
  await expect.poll(async () => {
    const response = await request.get(`${api}/api/conversations/${draft.id}/activity`, { headers });
    return (await response.json()).busy;
  }, { timeout: 120_000 }).toBe(false);
  const saved = await (await request.get(`${api}/api/drafts/${draft.id}`, { headers })).json();
  expect(saved.title).toContain("Sandbox Levi's");
});

test("the Mac window hands its account session to the native AI worker and clears it on logout", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "The native Mac bridge is desktop-only");
  await page.addInitScript(() => {
    const calls: { hasToken: boolean; owner: string | null }[] = [];
    Object.assign(window, {
      macAICalls: calls,
      pywebview: { api: { connect_chatgpt_worker: async (token: string | null, owner: string | null) => {
        calls.push({ hasToken: Boolean(token), owner });
        return { ok: true };
      } } },
    });
  });
  await page.goto("/");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const current = () => page.evaluate(() => (window as unknown as { macAICalls: { hasToken: boolean; owner: string | null }[] }).macAICalls.at(-1));
  await expect.poll(current).toMatchObject({ hasToken: true, owner: expect.stringMatching(/^[0-9a-f-]{36}$/) });
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect.poll(current).toMatchObject({ hasToken: false, owner: null });
});
