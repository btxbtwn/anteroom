import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;
const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2L8AAAAASUVORK5CYII=", "base64");

test("all five rewrites start before slow editor reads finish", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const login = await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const drafts: { id: string; title: string }[] = [];
  for (let index = 0; index < 5; index++) {
    const title = `Bulk rewrite ${info.project.name} ${index} ${Date.now()}`;
    const created = await request.post(`${api}/api/drafts`, { headers, data: { title } });
    expect(created.status()).toBe(201);
    const draft = await created.json();
    expect((await request.post(`${api}/api/drafts/${draft.id}/photos`, { headers, data: pixel })).status()).toBe(201);
    drafts.push({ id: draft.id, title });
  }
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await page.getByRole("button", { name: "Select listings", exact: true }).click();
  for (const draft of drafts) {
    await page.getByRole("checkbox", { name: `Select ${draft.title}`, exact: true }).click();
  }
  await page.getByRole("region", { name: "Bulk listing actions" }).getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(page.getByRole("button", { name: "Rewrite 5", exact: true })).toBeEnabled();
  const reset = new Set<string>();
  const started = new Set<string>();
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/conversations/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const [, , , id, endpoint] = path.split("/");
    if (!drafts.some((draft) => draft.id === id)) return route.continue();
    if (endpoint === "reset") {
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      reset.add(id!);
      return route.fulfill({ response });
    }
    if (endpoint === "generate") {
      started.add(id!);
      await held;
      return route.fulfill({ contentType: "text/event-stream", body: "data: Saved draft\n\ndata: [DONE]\n\n" });
    }
    if (reset.has(id!) && ["screen", "listing", "messages"].includes(endpoint || "")) await held;
    return route.continue();
  });
  try {
    await page.getByRole("button", { name: "Rewrite 5", exact: true }).click();
    await expect.poll(() => reset.size, { timeout: 30_000 }).toBe(5);
    await expect.poll(() => started.size, { timeout: 10_000 }).toBe(5);
    expect(started).toEqual(new Set(drafts.map((draft) => draft.id)));
    await expect(page.getByRole("button", { name: "Select listings", exact: true })).toBeEnabled();
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});
