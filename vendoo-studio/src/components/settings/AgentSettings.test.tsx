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
  installed: true,
  on_path: true,
  path: "/usr/local/bin/anteroom",
  version: "9.9.9",
  version_error: null,
  base_url: "http://127.0.0.1:4318",
  remote_base_url: "https://mac.tail.ts.net:9444",
  login_command: "anteroom auth login --alias local --base-url http://127.0.0.1:4318",
  remote_login_command: "anteroom auth login --alias studio --base-url https://mac.tail.ts.net:9444",
  install_command: null,
  hint: null,
  clients: [
    {
      id: "cursor",
      label: "Cursor",
      where: "Cursor → Settings → MCP",
      snippet: '{\n  "mcpServers": { "anteroom": { "command": "anteroom", "args": ["mcp", "serve"] } }\n}',
      command: null,
    },
    {
      id: "claude-desktop",
      label: "Claude Desktop",
      where: "Claude Desktop → Settings → Developer",
      snippet: '{\n  "mcpServers": { "anteroom": { "command": "anteroom", "args": ["mcp", "serve"] } }\n}',
      command: null,
    },
    {
      id: "claude-code",
      label: "Claude Code",
      where: "Run the command in a terminal",
      snippet: '{\n  "mcpServers": { "anteroom": { "command": "anteroom", "args": ["mcp", "serve"] } }\n}',
      command: "claude mcp add --transport stdio anteroom -- anteroom mcp serve",
    },
  ],
};

beforeEach(() => {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, data: null, error: null });
  useQuery.mockReturnValue({ data: status, isPending: false, error: null });
});

describe("Agent settings", () => {
  it("shows the command, addresses, and copy-ready client setup", () => {
    const html = renderToStaticMarkup(<AgentSettings />);
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
    useQuery.mockReturnValue({
      data: {
        ...status,
        installed: false,
        on_path: false,
        path: null,
        version: null,
        install_command: "pip install -e .",
        hint: "From the vendoo-studio folder, with its virtualenv active, run the install command. Then reopen this page.",
        remote_base_url: null,
        remote_login_command: null,
      },
      isPending: false,
      error: null,
    });
    const html = renderToStaticMarkup(<AgentSettings />);
    expect(html).toContain("Not installed.");
    expect(html).toContain("pip install -e .");
    expect(html).toContain("reopen this page");
  });
});
