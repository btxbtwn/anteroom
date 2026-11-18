import { test, expect } from "@playwright/test";

const api = `http://127.0.0.1:${process.env.VENDOO_STUDIO_PORT || "4341"}`;

// An edit records the seller's correction against the draft. Deleting the
// edited draft afterwards must still work (it answered 503 before #1593).
test("drafts edited in bulk can then be deleted in bulk", async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const login = await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const drafts: { id: string; title: string }[] = [];
  for (let index = 0; index < 2; index++) {
    const title = `Bulk edit delete ${info.project.name} ${index} ${Date.now()}`;
    const created = await request.post(`${api}/api/drafts`, { headers, data: { title } });
    expect(created.status()).toBe(201);
    const id = (await created.json()).id;
    const saved = await request.put(`${api}/api/conversations/${id}/listing`, {
      headers, data: { expected_revision_id: "", listing: { title, brand: "Acme", price: 30, quantity: 1 } },
    });
    expect(saved.status(), await saved.text()).toBe(200);
    drafts.push({ id, title });
  }
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const select = async () => {
    if (info.project.name === "phone") {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    }
    await page.getByRole("button", { name: "Select listings", exact: true }).click();
    for (const draft of drafts) await page.getByRole("checkbox", { name: `Select ${draft.title}`, exact: true }).click();
  };
  const bar = page.getByRole("region", { name: "Bulk listing actions" });

  await select();
  await bar.getByRole("button", { name: "Edit fields", exact: true }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByRole("checkbox", { name: "Replace Brand" }).click();
  await dialog.getByRole("textbox", { name: "Brand", exact: true }).fill("Audit Brand");
  await dialog.getByRole("button", { name: "Review changes", exact: true }).click();
  await dialog.getByRole("button", { name: "Save 2 listings", exact: true }).click();
  await expect(dialog.getByText("2 saved")).toBeVisible({ timeout: 30_000 });
  await dialog.getByRole("button", { name: "Done", exact: true }).click();

  await select();
  await bar.getByRole("button", { name: "More", exact: true }).click();
  await page.getByRole("menuitem", { name: "Delete…", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Review items", exact: true }).click();
  await dialog.getByRole("checkbox", { name: /I want to delete these 2 items/ }).click();
  await dialog.getByRole("button", { name: "Delete 2 items", exact: true }).click();
  await expect(dialog.getByText("2 deleted")).toBeVisible({ timeout: 30_000 });
  await info.attach("bulk-edit-delete.png", { body: await page.screenshot(), contentType: "image/png" });
  for (const draft of drafts) {
    expect((await request.get(`${api}/api/conversations/${draft.id}`, { headers })).status()).toBe(404);
  }
});
