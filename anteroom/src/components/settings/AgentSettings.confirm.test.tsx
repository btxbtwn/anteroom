// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import { api } from "../../api/client";
import {
  completeConfirmDialogClose,
  readConfirmDialogState,
  registerConfirmDialogHost,
  respondToConfirmDialog,
} from "../../ui/confirmDialog";
import { AgentSettings } from "./AgentSettings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  completeConfirmDialogClose();
  vi.restoreAllMocks();
});

describe("AgentSettings confirmations", () => {
  it("confirms before revoking an access token or disconnecting a connected app", async () => {
    vi.spyOn(api.settings.mcpTokens, "list").mockResolvedValue({ tokens: [] });
    vi.spyOn(api.settings.mcpConnections, "list").mockResolvedValue({ connections: [] });
    const revokeSpy = vi.spyOn(api.settings.mcpTokens, "revoke").mockResolvedValue({ ok: true });
    const disconnectSpy = vi.spyOn(api.settings.mcpConnections, "disconnect").mockResolvedValue({
      client_id: "conn_1",
      client_name: "ChatGPT",
      created_at: "2026-11-20T12:00:00Z",
      last_used_at: null,
      revoked_at: "2026-11-25T12:00:00Z",
      signed_out_at: "2026-11-25T12:00:00Z",
      permissions: { listings: "read", analytics: "read", attention: "read", marketplaces: "none" },
      signed_out: true,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
    });
    client.setQueryData(["settings-agent"], {
      hosted: true,
      installed: false,
      on_path: false,
      path: null,
      version: null,
      version_error: null,
      base_url: "https://myanteroom.com",
      mcp_url: "https://myanteroom.com/api/mcp",
      remote_base_url: null,
      login_command: "",
      remote_login_command: null,
      install_command: null,
      hint: null,
      remote_clients: [],
      clients: [],
    });
    client.setQueryData(["settings-mcp-tokens"], {
      tokens: [
        {
          id: "tok_1",
          name: "Claude on iPad",
          token_prefix: "arm_abc123",
          created_at: "2026-11-20T12:00:00Z",
          last_used_at: null,
          expires_at: null,
          revoked_at: null,
          permissions: { listings: "read", analytics: "read", attention: "read", marketplaces: "none" },
          active: true,
        },
      ],
    });
    client.setQueryData(["settings-mcp-connections"], {
      connections: [
        {
          client_id: "conn_1",
          client_name: "ChatGPT",
          created_at: "2026-11-20T12:00:00Z",
          last_used_at: null,
          revoked_at: null,
          signed_out_at: null,
          permissions: { listings: "read", analytics: "read", attention: "read", marketplaces: "none" },
        },
      ],
    });

    const unregister = registerConfirmDialogHost();
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(
          <WorkspaceRuntimeProvider
            value={{
              hosted: true,
              features: new Set(),
              views: new Set(["settings"]),
              marketplaceForms: new Set(),
              actionMarketplaces: new Set(),
            }}
          >
            <QueryClientProvider client={client}>
              <AgentSettings />
            </QueryClientProvider>
          </WorkspaceRuntimeProvider>,
        );
      });

      const revokeBtn = host.querySelector<HTMLButtonElement>('[aria-label="Revoke Claude on iPad"]');
      expect(revokeBtn).not.toBeNull();
      await act(async () => {
        revokeBtn!.click();
      });
      expect(revokeSpy).not.toHaveBeenCalled();
      let dialog = readConfirmDialogState();
      expect(dialog.status).toBe("confirming");
      expect(dialog.status === "confirming" && dialog.confirmLabel).toBe("Revoke token");
      await act(async () => {
        respondToConfirmDialog(false);
        completeConfirmDialogClose();
      });
      expect(revokeSpy).not.toHaveBeenCalled();

      await act(async () => {
        revokeBtn!.click();
      });
      await act(async () => {
        respondToConfirmDialog(true);
        completeConfirmDialogClose();
      });
      await vi.waitFor(() => {
        expect(revokeSpy).toHaveBeenCalledWith("tok_1", expect.anything());
      });

      const disconnectBtn = host.querySelector<HTMLButtonElement>('[aria-label="Disconnect ChatGPT"]');
      expect(disconnectBtn).not.toBeNull();
      await act(async () => {
        disconnectBtn!.click();
      });
      expect(disconnectSpy).not.toHaveBeenCalled();
      dialog = readConfirmDialogState();
      expect(dialog.status).toBe("confirming");
      expect(dialog.status === "confirming" && dialog.confirmLabel).toBe("Disconnect");
      await act(async () => {
        respondToConfirmDialog(true);
        completeConfirmDialogClose();
      });
      await vi.waitFor(() => {
        expect(disconnectSpy).toHaveBeenCalledWith("conn_1", expect.anything());
      });
    } finally {
      unregister();
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    }
  });
});
