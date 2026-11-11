import { test, expect } from "@playwright/test";

test("connector signs in, controls subscriptions, and disconnects without opening inventory", async ({ page }, info) => {
  const errors: string[] = [];
  const inventory: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => { if (new URL(request.url()).pathname === "/api/conversations") inventory.push(request.url()); });
  if (info.project.name === "desktop") await page.setViewportSize({ width: 660, height: 760 });
  await page.addInitScript(() => {
    const state = {
      connection: "connected", paused: false, active_calls: 0,
      chatgpt: { signed_in: false, listing_model: "gpt-model", pending: null },
      claude: { signed_in: false, installed: true, listing_model: "sonnet" },
    };
    Object.assign(window, { pywebview: { api: {
      connect_chatgpt_worker: async () => ({ ok: true }),
      connector_status: async () => structuredClone(state),
      connector_pause: async (paused: boolean) => { state.paused = paused; },
      connector_chatgpt_login: async () => { state.chatgpt.signed_in = true; },
      connector_chatgpt_logout: async () => { state.chatgpt.signed_in = false; },
      connector_models: async (provider: "chatgpt" | "claude") => [{ value: state[provider].listing_model, label: state[provider].listing_model }, { value: "fast-model", label: "Fast model" }],
      connector_set_model: async (provider: "chatgpt" | "claude", model: string) => { state[provider].listing_model = model; },
      connector_claude_start: async () => ({ id: "sandbox-login" }),
      connector_claude_read: async () => { state.claude.signed_in = true; return { code: 0 }; },
      connector_claude_close: async () => ({}),
      connector_claude_send: async () => ({}),
    } } });
  });
  await page.goto("/?connector&sign-in");
  await page.getByLabel("Email address").fill("alice@sandbox.test");
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Anteroom Connector", exact: true })).toBeVisible();
  await expect(page.getByText("Connected to Anteroom", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sign in", exact: true }).first().click();
  await expect(page.getByText("Signed in with your subscription", { exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Signed in with your subscription", { exact: true })).toHaveCount(2);
  await page.getByRole("button", { name: "Load models", exact: true }).first().click();
  await page.getByRole("combobox", { name: "ChatGPT model", exact: true }).click();
  await page.getByRole("option", { name: "Fast model", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "ChatGPT model", exact: true })).toContainText("Fast model");
  await page.screenshot({ path: `e2e/results/hosted/connector-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
  await expect(page.getByText("Paused. Current requests can finish; new requests are stopped.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Disconnect Anteroom account", exact: true }).click();
  await expect(page.getByLabel("Email address")).toBeVisible();
  expect(new URL(page.url()).searchParams.has("connector")).toBe(true);
  expect(inventory).toEqual([]);
  expect(errors).toEqual([]);
});
