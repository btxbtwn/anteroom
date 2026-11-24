import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.ANTEROOM_PORT || "4341"}`;

test("a hosted draft's General view offers Ready to list and scheduling", async ({ page, request }, info) => {
  const login = await request.post(`${api}/local/auth/v1/token?grant_type=password`, { data: { email: "alice@sandbox.test", password: "sandbox" } });
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
  // A fresh draft: the sample listings include sold items, which never offer scheduling.
  const created = await request.post(`${api}/api/drafts`, { headers, data: { title: `Schedule ${info.project.name} ${Date.now()}` } });
  expect(created.status()).toBe(201);
  const draft = await created.json();
  expect((await request.get(`${api}/api/conversations/${draft.id}/schedule`, { headers })).status()).toBe(200);

  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await page.goto(`/?listing=${draft.id}`);
  const ready = page.getByRole("switch", { name: "Mark draft ready to list" });
  await expect(ready).toBeVisible();
  await expect(page.getByText("Ready to list & schedule publish")).toBeVisible();
  await ready.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `e2e/results/hosted/draft-schedule-${info.project.name}.png` });
});
