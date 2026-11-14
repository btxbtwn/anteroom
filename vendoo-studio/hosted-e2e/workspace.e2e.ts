import { randomUUID } from "node:crypto";
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
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  // Signed in, the assistant is home.
  await expect(page.getByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening)$/ })).toBeVisible();
  await expect(page.getByRole("list", { name: "Example questions" })).toBeVisible();
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
  }
  await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true }).click();
  await page.getByRole("button", { name: "Account", exact: true }).click();
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
  await page.goto("/?sign-in");
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
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: title, exact: true }).click();

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

test("two editors generate while a third saves without preloading unused marketplace forms", async ({ page, context, request }, info) => {
  test.skip(info.project.name === "phone", "Multiple independent browser tabs exercise the shared hosted API");
  test.setTimeout(240_000);
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const drafts: { id: string }[] = [];
  for (let index = 0; index < 3; index++) {
    const created = await request.post(`${api}/api/drafts`, { headers, data: { title: `Concurrent editor ${index} ${Date.now()}` } });
    expect(created.status()).toBe(201);
    const draft = await created.json();
    drafts.push(draft);
    if (index < 2) {
      for (let photo = 0; photo < 4; photo++) {
        expect((await request.post(`${api}/api/drafts/${draft.id}/photos`, { headers, data: pixel })).status()).toBe(201);
      }
    }
  }
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  const editors = [page, await context.newPage(), await context.newPage()];
  const failures: string[] = [];
  const unusedForms: string[] = [];
  for (const editor of editors) {
    editor.on("response", response => {
      if (response.url().includes("/api/") && response.status() >= 500) failures.push(`${response.status()} ${new URL(response.url()).pathname}`);
    });
    editor.on("request", request => {
      if (/\/native-marketplaces\/[^/]+\/form(?:\?|$)/.test(request.url())) unusedForms.push(new URL(request.url()).pathname);
    });
  }
  // Document load does not await the editor's asynchronous data. Keep the
  // network deadline separate from the five-second rendered-value assertions.
  const openEditor = async (editor: typeof page, draft: { id: string }) => {
    const loaded = editor.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/conversations/${draft.id}/screen`
      && response.request().method() === "GET", { timeout: 30_000 });
    await editor.goto(`/?listing=${draft.id}`);
    const response = await loaded;
    expect(response.status()).toBe(200);
    const screen = await response.json();
    expect(screen.conversation.id).toBe(draft.id);
    return screen;
  };
  try {
    await Promise.all(editors.map(async (editor, index) => {
      await openEditor(editor, drafts[index]);
      await expect(editor.locator(`#item-notes-${drafts[index].id}`)).toBeVisible();
    }));
    await Promise.all(editors.slice(0, 2).map(editor => editor.getByRole("button", { name: "Generate listing", exact: true }).click()));
    await expect.poll(async () => Promise.all(drafts.slice(0, 2).map(async draft => {
      return (await (await request.get(`${api}/api/conversations/${draft.id}/activity`, { headers })).json()).busy;
    })), { timeout: 30_000 }).toEqual([true, true]);
    const marker = "Saved while both other drafts generate";
    const saved = editors[2].waitForResponse(response => response.url().includes(`/api/conversations/${drafts[2].id}`)
      && response.request().method() === "PATCH");
    await editors[2].locator(`#item-notes-${drafts[2].id}`).fill(marker);
    expect((await saved).status()).toBe(200);
    await editors[2].route(`**/api/conversations/${drafts[2].id}/screen`, async route => {
      // Reproduce a core read finishing after the default five-second UI assertion.
      await new Promise(resolve => setTimeout(resolve, 7000));
      await route.continue();
    }, { times: 1 });
    const reloaded = await openEditor(editors[2], drafts[2]);
    expect(JSON.parse(reloaded.conversation.notes).sellerNotes).toBe(marker);
    await expect(editors[2].locator(`#item-notes-${drafts[2].id}`)).toHaveValue(marker);
    for (let index = 0; index < 2; index++) {
      await expect(editors[index].getByRole("textbox", { name: "Title", exact: true }).first()).toHaveValue(/Sandbox Levi's/, { timeout: 150_000 });
      await expect.poll(async () => (await (await request.get(`${api}/api/conversations/${drafts[index].id}/activity`, { headers })).json()).busy,
        { timeout: 150_000 }).toBe(false);
      await openEditor(editors[index], drafts[index]);
      await expect(editors[index].getByRole("textbox", { name: "Title", exact: true }).first()).toHaveValue(/Sandbox Levi's/);
    }
    expect(unusedForms).toEqual([]);
    expect(failures).toEqual([]);
  } finally {
    await Promise.all(editors.slice(1).map(editor => editor.close()));
  }
});

test("typing before the initial details arrive preserves the pending edit and untouched fields", async ({ page, request }, info) => {
  test.skip(info.project.name === "phone", "The same details autosave serves both layouts");
  test.setTimeout(60_000);
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: {
    title: "Delayed details", notes: JSON.stringify({ sellerNotes: "Original notes", sku: "KEEP-123", knownFlaws: "Keep this mark" }),
  } })).json();
  const otherTitle = `Other details draft ${Date.now()}`;
  await request.post(`${api}/api/drafts`, { headers, data: { title: otherTitle } });
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/api/conversations/${draft.id}/screen`, async route => {
    await delayed;
    await route.continue();
  });
  try {
    await page.goto(`/?listing=${draft.id}`);
    const notes = page.locator(`#item-notes-${draft.id}`);
    await notes.fill("Typed while the initial read is pending");
    release();
    await expect.poll(async () => {
      const response = await request.get(`${api}/api/conversations/${draft.id}`, { headers });
      return JSON.parse((await response.json()).notes || "{}").sellerNotes;
    }, { timeout: 15_000 }).toBe("Typed while the initial read is pending");
    const saved = JSON.parse((await (await request.get(`${api}/api/conversations/${draft.id}`, { headers })).json()).notes);
    expect(saved.sku).toBe("KEEP-123");
    expect(saved.knownFlaws).toBe("Keep this mark");
    await notes.fill("Saved after moving to another draft");
    await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: otherTitle, exact: true }).click();
    await expect.poll(async () => {
      const response = await request.get(`${api}/api/conversations/${draft.id}`, { headers });
      return JSON.parse((await response.json()).notes || "{}").sellerNotes;
    }, { timeout: 15_000 }).toBe("Saved after moving to another draft");
    await page.goto(`/?listing=${draft.id}`);
    await expect(page.locator(`#item-notes-${draft.id}`)).toHaveValue("Saved after moving to another draft");
  } finally { release(); }
});

test("a sold listing offers no photo upload, which hosted accepts on drafts only", async ({ page, request }, info) => {
  test.skip(info.project.name === "phone", "The same photo tray serves both layouts");
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: { title: `Sold photos ${Date.now()}` } })).json();
  expect((await request.post(`${api}/api/drafts/${draft.id}/photos`, { headers, data: pixel })).status()).toBe(201);
  expect((await request.post(`${api}/api/conversations/${draft.id}/marketplace-listings/sold`, { headers, data: {
    marketplace: "ebay", currency: "USD", item_revenue: "20", fees: "2", shipping_credit: null, shipping_cost: null,
    item_cost: null, sold_on: new Date().toISOString().slice(0, 10), request_id: randomUUID().replaceAll("-", ""),
  } })).ok()).toBe(true);
  // Reading the inventory reconciles the stored status the upload route checks.
  await expect.poll(async () => {
    await request.get(`${api}/api/conversations`, { headers });
    return (await (await request.get(`${api}/api/conversations/${draft.id}`, { headers })).json()).status;
  }, { timeout: 15_000 }).toBe("sold");
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  await page.goto(`/?listing=${draft.id}`);
  const tray = page.locator("[data-photo-tray]");
  await expect(tray.getByText("New photos can be added while this listing is a draft.", { exact: true })).toBeVisible();
  await expect(tray.getByRole("button", { name: "Add Photos" })).toHaveCount(0);
  await expect(tray.getByRole("button", { name: "Add Folder" })).toHaveCount(0);
});

test("a listing the server briefly cannot answer keeps loading and fills in its saved details once it is back, without an error or a Retry tap", async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const alice = await (await request.post(`${api}/local/auth/v1/token?grant_type=password`, {
    data: { email: "alice@sandbox.test", password: "sandbox" },
  })).json();
  const headers = { Authorization: `Bearer ${alice.access_token}` };
  const draft = await (await request.post(`${api}/api/drafts`, { headers, data: {
    title: `Unavailable details ${info.project.name} ${Date.now()}`,
    notes: JSON.stringify({ vendooLabels: "A23", sellerNotes: "Saved notes" }),
  } })).json();
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Account", exact: true })).toBeVisible();
  // The hosted API answers this way when its database connections run out.
  let unavailable = true;
  let answered503 = 0;
  await page.route(`**/api/conversations/${draft.id}**`, async route => {
    if (!unavailable || route.request().method() !== "GET") return route.continue();
    answered503 += 1;
    await route.fulfill({ status: 503, contentType: "application/json", headers: { "Retry-After": "5" },
      body: JSON.stringify({ detail: "Cloud data is temporarily unavailable" }) });
  });
  if (info.project.name === "phone") {
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("navigation", { name: "Pages" }).getByRole("button", { name: "Inventory" }).click();
    await page.getByRole("list", { name: "Listings" }).getByRole("link", { name: draft.title, exact: true }).click();
    await page.getByRole("tab", { name: "Listing", exact: true }).click();
  } else {
    await page.goto(`/?listing=${draft.id}`);
  }
  // A deploy or a full pool passes like a slow read: no error, and the saved details arrive.
  await expect.poll(() => answered503, { timeout: 15_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(2_000);
  await expect(page.getByRole("alert").filter({ hasText: "This listing didn't load" })).toHaveCount(0);
  unavailable = false;
  // The read is asked again after Retry-After, with no tap.
  await expect(page.locator(`#item-notes-${draft.id}`)).toHaveValue("Saved notes", { timeout: 20_000 });
  await expect(page.getByText("A23", { exact: true })).toBeVisible();
});

test("the Mac window hands its account session to the native AI worker and clears it on logout", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "The native Mac bridge is desktop-only");
  // Logout replaces the document; observe the native boundary across navigation.
  const calls: { hasToken: boolean; owner: string | null }[] = [];
  await page.exposeFunction("recordMacAICall", (hasToken: boolean, owner: string | null) => {
    calls.push({ hasToken, owner });
    return { ok: true };
  });
  await page.addInitScript(() => {
    Object.assign(window, {
      pywebview: { api: { connect_chatgpt_worker: async (token: string | null, owner: string | null) => {
        return (window as unknown as { recordMacAICall: (hasToken: boolean, owner: string | null) => Promise<{ ok: boolean }> }).recordMacAICall(Boolean(token), owner);
      } } },
    });
  });
  await page.goto("/?sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const current = () => calls.at(-1);
  await expect.poll(current).toMatchObject({ hasToken: true, owner: expect.stringMatching(/^[0-9a-f-]{36}$/) });
  // The worker receives auth before workspace startup finishes. Wait for the
  // interactive home screen so startup cannot replace an already-open menu.
  await expect(page.getByRole("list", { name: "Example questions" })).toBeVisible();
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByLabel("Email address")).toBeVisible();
  await expect.poll(current).toMatchObject({ hasToken: false, owner: null });
});
