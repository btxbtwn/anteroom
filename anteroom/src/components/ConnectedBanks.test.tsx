// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConnectionStatus, PlaidStatus } from "../api/contract.gen";
import { api } from "../api/client";
import { readConfirmDialogState, registerConfirmDialogHost, respondToConfirmDialog } from "../ui/confirmDialog";
import { ConnectedBanks } from "./ConnectedBanks";

vi.mock("../api/client", () => ({
  api: { plaid: { status: vi.fn(), linkToken: vi.fn(), exchange: vi.fn(), sync: vi.fn(), disconnect: vi.fn() } },
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const bank: ConnectionStatus = {
  id: "bank", institution_name: "Test Bank", accounts: [{ name: "Checking", mask: "7777", type: "depository" }],
  has_cursor: true, status: "current", last_synced_at: "2026-11-21T13:00:00Z", last_error: null,
};
const empty: PlaidStatus = { configured: true, max_connections: 2, connections: [] };

let container: HTMLDivElement;
let root: Root;
let queries: QueryClient;

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

async function render(status: PlaidStatus) {
  vi.mocked(api.plaid.status).mockResolvedValue(status);
  await act(async () => {
    root.render(<QueryClientProvider client={queries}><ConnectedBanks /></QueryClientProvider>);
  });
  await settle();
}

function button(label: string) {
  const match = Array.from(container.querySelectorAll("button")).find((element) => element.textContent === label);
  if (!match) throw new Error(`Button missing: ${label}`);
  return match;
}

describe("Connected banks and cards", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => {
    await act(async () => root.unmount());
    queries.clear();
    container.remove();
  });

  it("explains server setup when Plaid is unconfigured", async () => {
    await render({ ...empty, configured: false });
    expect(container.textContent).toContain("PLAID_CLIENT_ID");
    expect(container.textContent).toContain("0/2 connected");
    expect(container.querySelector("button")).toBeNull();
  });

  it("offers to connect an institution without preloading Plaid", async () => {
    await render(empty);
    expect(container.textContent).toContain("up to two years of expenses");
    expect(button("Connect bank or card").disabled).toBe(false);
    expect(api.plaid.linkToken).not.toHaveBeenCalled();
    expect(document.querySelector('script[src*="cdn.plaid.com"]')).toBeNull();
  });

  it("opens Plaid and exchanges the returned token", async () => {
    const open = vi.fn();
    const destroy = vi.fn();
    const create = vi.fn(({ onSuccess }: {
      onSuccess: (token: string, metadata: { institution: { name: string } }) => void;
    }) => ({
      open: () => { open(); onSuccess("public-token", { institution: { name: "Test Bank" } }); },
      destroy,
    }));
    Object.assign(window, { Plaid: { create } });
    try {
      await render(empty);
      vi.mocked(api.plaid.linkToken).mockResolvedValue({ link_token: "link-token" });
      vi.mocked(api.plaid.exchange).mockResolvedValue(bank);
      vi.mocked(api.plaid.status).mockResolvedValue({ ...empty, connections: [bank] });
      await act(async () => button("Connect bank or card").click());
      await settle();
      expect(create).toHaveBeenCalledWith(expect.objectContaining({ token: "link-token" }));
      expect(open).toHaveBeenCalled();
      expect(destroy).toHaveBeenCalled();
      expect(api.plaid.exchange).toHaveBeenCalledWith({ public_token: "public-token", institution_name: "Test Bank" }, expect.anything());
      expect(container.textContent).toContain("1/2 connected");
    } finally {
      Reflect.deleteProperty(window, "Plaid");
    }
  });

  it("shows masks and sync time, syncs on request, and confirms disconnect", async () => {
    const unregister = registerConfirmDialogHost();
    try {
      await render({ ...empty, connections: [bank] });
      expect(container.textContent).toContain("Checking •••• 7777");
      expect(container.textContent).toContain("Last synced");
      vi.mocked(api.plaid.sync).mockResolvedValue(bank);
      await act(async () => button("Sync now").click());
      await settle();
      expect(api.plaid.sync).toHaveBeenCalledWith("bank");
      await act(async () => button("Disconnect").click());
      expect(readConfirmDialogState()).toMatchObject({ message: expect.stringContaining("stay in your books") });
      expect(api.plaid.disconnect).not.toHaveBeenCalled();
      vi.mocked(api.plaid.status).mockResolvedValue(empty);
      await act(async () => respondToConfirmDialog(true));
      await settle();
      expect(api.plaid.disconnect).toHaveBeenCalledWith("bank");
      expect(container.textContent).toContain("0/2 connected");
    } finally {
      unregister();
    }
  });

  it("disables connecting at the two-institution limit", async () => {
    await render({ ...empty, connections: [bank, { ...bank, id: "other", institution_name: "Other Bank" }] });
    expect(container.textContent).toContain("2/2 connected");
    expect(button("Connect bank or card").disabled).toBe(true);
  });
});
