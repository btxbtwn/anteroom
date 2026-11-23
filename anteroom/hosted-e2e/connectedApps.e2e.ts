import { test, expect, type Page } from "@playwright/test";

const API = `http://127.0.0.1:${process.env.ANTEROOM_PORT || "4341"}`;
const CALLBACK = "https://claude.invalid/oauth/callback";

async function signIn(page: Page, email: string) {
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill("sandbox");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}

async function mcpTools(page: Page, token: string): Promise<{ status: number; tools: string[] }> {
  const response = await page.request.post(`${API}/api/mcp`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { jsonrpc: "2.0", id: 1, method: "tools/list" },
  });
  if (!response.ok()) return { status: response.status(), tools: [] };
  const body = await response.json();
  return { status: response.status(), tools: body.result.tools.map((tool: { name: string }) => tool.name).sort() };
}

test("an app signs in, gets only the access the seller chose, and stops working once disconnected", async ({ page }, info) => {
  // Bob on phone, Alice on desktop: each project starts from its own grant.
  const email = info.project.name === "phone" ? "bob@sandbox.test" : "alice@sandbox.test";
  let returned = "";
  await page.route(`${CALLBACK}**`, route => { returned = route.request().url(); return route.fulfill({ body: "Back in Claude" }); });

  // Signed out, the approval link asks for sign-in and then shows the request.
  await page.goto("/oauth/consent?authorization_id=sandbox-claude");
  await signIn(page, email);
  await expect(page.getByRole("heading", { name: "Connect Claude to Anteroom" })).toBeVisible();
  await expect(page.getByText(/never publish, update live listings or delist/)).toBeVisible();
  await expect(page.getByText("After you answer, you go back to claude.invalid.")).toBeVisible();
  await page.getByRole("combobox", { name: "Claude Listings access" }).click();
  await page.getByRole("option", { name: "Read and write" }).click();
  await page.screenshot({ path: `e2e/results/hosted/app-consent-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Allow", exact: true }).click();
  await expect.poll(() => returned).toContain("code=");

  // The app trades its code for a token, as Claude would.
  const code = new URL(returned).searchParams.get("code") ?? "";
  const tokenResponse = await page.request.post(`${API}/local/auth/v1/oauth/token`, {
    form: { grant_type: "authorization_code", code },
  });
  const token = (await tokenResponse.json()).access_token as string;
  const connected = await mcpTools(page, token);
  expect(connected.status).toBe(200);
  expect(connected.tools).toContain("create_draft_listing");
  // Its token reaches nothing but the MCP endpoint.
  expect((await page.request.get(`${API}/api/settings/mcp-connections`, { headers: { Authorization: `Bearer ${token}` } })).status()).toBe(401);

  // Settings → Agents lists it; disconnecting stops its next call.
  await page.goto("/");
  if (info.project.name === "phone") await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  if (info.project.name === "phone") {
    await page.getByRole("combobox", { name: "Search settings" }).fill("connected apps");
    await page.getByRole("option", { name: /Connected apps/ }).click();
  } else {
    await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Agents", exact: true }).click();
  }
  const row = page.locator("#agent-mcp-connections");
  await expect(row.getByText("Claude", { exact: true })).toBeVisible();
  await expect(row.getByText("Connected", { exact: true })).toBeVisible();
  await row.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `e2e/results/hosted/connected-apps-${info.project.name}.png` });
  await row.getByRole("button", { name: "Disconnect Claude" }).click();
  await expect(row.getByText("Disconnected", { exact: true })).toBeVisible();
  // Supabase confirmed the sign-out, so nothing is left to finish here.
  await expect(row.getByText("To use it again, connect it from Claude.")).toBeVisible();
  expect((await mcpTools(page, token)).status).toBe(403);

  // Connecting again goes through the approval page; the app is back on only once it is approved.
  returned = "";
  await page.goto("/oauth/consent?authorization_id=sandbox-claude");
  await page.getByRole("button", { name: "Allow", exact: true }).click();
  await expect.poll(() => returned).toContain("code=");
  // Back on, with what was chosen this time: read-only by default.
  const reconnected = await mcpTools(page, token);
  expect(reconnected.status).toBe(200);
  expect(reconnected.tools).toContain("search_inventory");
  expect(reconnected.tools).not.toContain("create_draft_listing");
});
