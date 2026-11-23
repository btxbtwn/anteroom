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
