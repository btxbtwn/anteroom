import type { AgentConnectionStatus } from "../src/api/agentConnection";
import { expect, test } from "./fixtures";

test("Agents copies the setup prompt for every HTTP and local MCP client", async ({ page, request, context, browserName, phone }, info) => {
  const response = await request.get("/api/settings/agent");
  expect(response.ok()).toBe(true);
  const { clients, remote_clients }: AgentConnectionStatus = await response.json();
  expect(remote_clients?.map((client) => client.id)).toEqual(["claude", "chatgpt", "antigravity", "generic"]);
  expect(clients.map((client) => client.id)).toEqual(["cursor", "claude-desktop", "claude-code"]);

  if (browserName === "chromium") {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  } else {
    // WebKit does not grant clipboard-read to automation. Record each successful write.
    await page.addInitScript(() => {
      const write = navigator.clipboard.writeText.bind(navigator.clipboard);
      navigator.clipboard.writeText = async (text) => {
        await write(text);
        sessionStorage.setItem("copied-prompt", text);
      };
    });
  }
  await page.route(/\/api\/updates(?:\/progress)?$/, route => route.fulfill({ json: { available: false, status: "idle" } }));

  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Agents" }).click();
  await page.getByRole("heading", { name: "Google Antigravity" }).evaluate((heading) => heading.scrollIntoView({ block: "start" }));
  await info.attach("agent-setup-prompts.png", { body: await page.screenshot({ scale: "css" }), contentType: "image/png" });

  for (const client of [...(remote_clients ?? []), ...clients]) {
    const button = page.getByRole("button", { name: `Copy setup prompt for ${client.label}`, exact: true });
    await button.click();
    await expect(button).toHaveText("Copied");
    const copied = await page.evaluate((readClipboard) =>
      readClipboard ? navigator.clipboard.readText() : sessionStorage.getItem("copied-prompt"), browserName === "chromium");
    expect(copied).toBe(client.prompt);
  }
});

test("a new MCP token cannot act on marketplaces until the seller chooses it", async ({ page, request, phone }, info) => {
  await page.route(/\/api\/updates(?:\/progress)?$/, route => route.fulfill({ json: { available: false, status: "idle" } }));
  const name = `Marketplace agent ${info.project.name}`;
  const created = await request.post("/api/settings/mcp-tokens", { data: { name } });
  expect(created.ok()).toBe(true);
  const token = await created.json();
  expect(token.permissions.marketplaces).toBe("none");

  await page.goto("/");
  if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Agents" }).click();
  await expect(page.getByRole("heading", { name: "Let an AI app work in Anteroom" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "App setup" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "New token Marketplaces access" })).toHaveText(/No access/);
  const select = page.getByRole("combobox", { name: `${name} Marketplaces access` });
  await expect(select).toHaveText(/No access/);
  await select.click();
  await page.getByRole("option", { name: "Publish, update and delist" }).click();
  await expect(select).toHaveText(/Publish, update and delist/);
  await select.scrollIntoViewIfNeeded();
  await info.attach("agent-marketplaces-permission.png", { body: await page.screenshot({ scale: "css" }), contentType: "image/png" });

  const listed = await (await request.get("/api/settings/mcp-tokens")).json();
  const saved = listed.tokens.find((row: { id: string }) => row.id === token.id);
  expect(saved.permissions.marketplaces).toBe("publish");

  // Revoking a token requires confirmation via confirmDialog.
  await page.getByRole("button", { name: `Revoke ${name}`, exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Revoke token", exact: true }).click();
  await expect(page.getByRole("button", { name: `Revoke ${name}`, exact: true })).toHaveCount(0);
});
