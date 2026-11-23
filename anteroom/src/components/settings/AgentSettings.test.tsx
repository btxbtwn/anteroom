import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentConnectionStatus } from "../../api/agentConnection";
import { AgentSettings } from "./AgentSettings";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useQuery,
  useMutation,
}));

const status: AgentConnectionStatus = {
  hosted: false,
  installed: true,
  on_path: true,
  path: "/usr/local/bin/anteroom",
  version: "9.9.9",
  version_error: null,
  base_url: "http://127.0.0.1:4318",
  mcp_url: "http://127.0.0.1:4318/api/mcp",
  remote_base_url: "https://mac.tail.ts.net:9444",
  login_command: "anteroom auth login --alias local --base-url http://127.0.0.1:4318",
  remote_login_command: "anteroom auth login --alias studio --base-url https://mac.tail.ts.net:9444",
  install_command: null,
  hint: null,
  remote_clients: [
    {
      id: "claude",
      label: "Claude",
      where: "Claude → Settings → Connectors → Add custom connector",
      snippet: "Server URL: http://127.0.0.1:4318/api/mcp\nHeader: Authorization: Bearer <your-token>",
      command: "claude mcp add --transport http anteroom http://127.0.0.1:4318/api/mcp --header \"Authorization: Bearer <your-token>\"",
      prompt: "Set up Claude over Streamable HTTP.",
    },
    {
      id: "chatgpt",
      label: "Codex / ChatGPT",
      where: "Merge this into the Codex config.toml.",
      snippet: '[mcp_servers.anteroom]\nurl = "http://127.0.0.1:4318/api/mcp"',
      command: null,
      prompt: "Set up Codex over Streamable HTTP.",
    },
    {
      id: "antigravity",
      label: "Google Antigravity",
      where: "Antigravity IDE → MCP Servers → Manage MCP Servers",
      snippet: '{"mcpServers": {"anteroom": {"serverUrl": "http://127.0.0.1:4318/api/mcp"}}}',
      command: null,
      prompt: "Set up Google Antigravity over Streamable HTTP.",
    },
    {
      id: "generic",
      label: "Other MCP clients",
      where: "Use your client's setup format for a Streamable HTTP MCP server.",
      snippet: "Server URL: http://127.0.0.1:4318/api/mcp",
      command: null,
      prompt: "Set up Anteroom over Streamable HTTP using your MCP client.",
    },
  ],
  clients: [
    {
      id: "cursor",
      label: "Cursor",
      where: "Cursor → Settings → MCP",
      snippet: '{\n  "mcpServers": { "anteroom": { "command": "anteroom", "args": ["mcp", "serve"] } }\n}',
      command: null,
      prompt: "Set up Cursor over local stdio.",
    },
    {
      id: "claude-desktop",
      label: "Claude Desktop",
      where: "Claude Desktop → Settings → Developer",
      snippet: '{\n  "mcpServers": { "anteroom": { "command": "anteroom", "args": ["mcp", "serve"] } }\n}',
      command: null,
      prompt: "Set up Claude Desktop over local stdio.",
    },
    {
      id: "claude-code",
      label: "Claude Code",
      where: "Run the command in a terminal",
      snippet: '{\n  "mcpServers": { "anteroom": { "command": "anteroom", "args": ["mcp", "serve"] } }\n}',
      command: "claude mcp add --transport stdio anteroom -- anteroom mcp serve",
      prompt: "Set up Claude Code over local stdio.",
    },
  ],
};

function mockQueries(agentData: AgentConnectionStatus, tokens: Array<{ id: string; name: string; token_prefix: string; created_at: string | null; last_used_at: string | null; expires_at: string | null; revoked_at: string | null }> = []) {
  useQuery.mockImplementation((opts: { queryKey?: unknown[] }) => {
    if (opts?.queryKey?.[0] === "settings-mcp-tokens") {
      return { data: { tokens }, isPending: false, error: null, refetch: vi.fn() };
    }
    return { data: agentData, isPending: false, error: null };
  });
}

beforeEach(() => {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, data: null, error: null });
  mockQueries(status);
});

describe("Agent settings", () => {
  it("shows the command, addresses, remote MCP setup, and copy-ready client snippets", () => {
    const html = renderToStaticMarkup(<AgentSettings />);
    expect(html).toContain("Remote MCP server");
    expect(html).toContain("http://127.0.0.1:4318/api/mcp");
    expect(html).toContain("Claude");
    expect(html).toContain("ChatGPT");
    for (const client of [...(status.remote_clients ?? []), ...status.clients]) {
      expect(html).toContain(`aria-label="Copy setup prompt for ${client.label}"`);
    }
    expect(html).toContain("Access tokens");
    expect(html).toContain("anteroom command");
    expect(html).toContain("Installed and on your PATH, version 9.9.9.");
    expect(html).toContain("9.9.9");
    expect(html).toContain("/usr/local/bin/anteroom");
    expect(html).toContain("http://127.0.0.1:4318");
    expect(html).toContain("anteroom auth login --alias local --base-url http://127.0.0.1:4318");
    expect(html).toContain("https://mac.tail.ts.net:9444");
    expect(html).toContain("Test connection");
    expect(html).toContain("Cursor");
    expect(html).toContain("Claude Desktop");
    expect(html).toContain("Claude Code");
    expect(html).toContain("claude mcp add --transport stdio anteroom -- anteroom mcp serve");
    expect(html).toContain("mcp&quot;, &quot;serve");
    expect(html).not.toContain("pip install -e");
  });

  it("shows the install command when the CLI is missing", () => {
    mockQueries({
      ...status,
      installed: false,
      on_path: false,
      path: null,
      version: null,
      install_command: "pip install -e .",
      hint: "From the anteroom folder, with its virtualenv active, run the install command. Then reopen this page.",
      remote_base_url: null,
      remote_login_command: null,
    });
    const html = renderToStaticMarkup(<AgentSettings />);
    expect(html).toContain("Not installed.");
    expect(html).toContain("pip install -e .");
    expect(html).toContain("reopen this page");
  });

  it("renders hosted remote MCP controls and active tokens without local Mac CLI instructions", () => {
    mockQueries(
      {
        ...status,
        hosted: true,
        installed: false,
        on_path: false,
        path: null,
        version: null,
        base_url: "https://myanteroom.com",
        mcp_url: "https://myanteroom.com/api/mcp",
        remote_base_url: null,
        login_command: "",
        remote_login_command: null,
        clients: [],
      },
      [
        {
          id: "tok_1",
          name: "Claude Desktop",
          token_prefix: "arm_abc12345",
          created_at: "2026-11-20T12:00:00Z",
          last_used_at: null,
          expires_at: null,
          revoked_at: null,
        },
      ],
    );
    const html = renderToStaticMarkup(<AgentSettings />);
    expect(html).toContain("https://myanteroom.com/api/mcp");
    expect(html).toContain("Claude Desktop");
    expect(html).toContain("arm_abc12345");
    expect(html).toContain("ChatGPT");
    expect(html).toContain("Copy setup prompt for Google Antigravity");
    expect(html).toContain("Copy setup prompt for Other MCP clients");
    expect(html).not.toContain("Copy setup prompt for Cursor");
  });
});
