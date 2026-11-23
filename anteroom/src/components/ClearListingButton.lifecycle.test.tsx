// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { api } from "../api/client";
import {
  completeConfirmDialogClose,
  readConfirmDialogState,
  registerConfirmDialogHost,
  respondToConfirmDialog,
} from "../ui/confirmDialog";
import { holdRewrite, RegenerateListingButton, releaseRewrite } from "./ClearListingButton";
import { waitlist } from "./MarketplaceActivity";
import { toastManager } from "./ui/toast";

vi.mock("../app/runtime", () => ({
  useWorkspaceRuntime: () => ({ actionMarketplaces: new Set(["ebay"]) }),
}));
vi.mock("./RegeneratePriceDialog", () => ({ RegeneratePriceDialog: () => null }));
vi.mock("./MarketplaceActivity", () => ({ waitlist: { add: vi.fn() } }));
vi.mock("./ui/toast", () => ({ toastManager: { add: vi.fn() } }));

it.each(["reset", "refresh"] as const)("releases the rewrite hold after unmount and a failed %s", async (failure) => {
  const convId = "coat-" + failure;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const unregister = registerConfirmDialogHost();
  const container = document.createElement("div");
  const root = createRoot(container);
  let mounted = true;
  const timestamp = "2026-01-01T00:00:00Z";
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.clearAllMocks();
  vi.spyOn(api.conversations, "photos").mockResolvedValue([{
    id: "photo", conversation_id: convId, original_filename: "coat.jpg", mime_type: "image/jpeg",
    size_bytes: 10, display_order: 0, width: null, height: null, created_at: timestamp, url: "/coat.jpg",
  }]);
  vi.spyOn(api.listings, "get")
    .mockResolvedValueOnce({
      conversation_id: convId, current_revision_id: "rev-1", revision_count: 1, can_send: false,
      listing: { title: "Wool coat" },
    })
    .mockRejectedValue(new Error("Refresh failed"));
  vi.spyOn(api.conversations, "messages").mockResolvedValue([]);
  const getLiveListings = vi.spyOn(api.marketplaceListings, "get").mockResolvedValue({
    ebay: { url: null, listing_id: "1", source: "anteroom", sold_here: false, state: "live" },
  });
  let finishReset!: () => void;
  const pending = new Promise<void>((resolve) => { finishReset = resolve; });
  const reset = vi.spyOn(api.conversations, "reset").mockImplementation(async () => {
    await pending;
    if (failure === "reset") throw new Error("Reset failed");
    return {
      id: convId, title: "Wool coat", status: "draft", created_at: timestamp, updated_at: timestamp, notes: null,
    };
  });

  try {
    await act(async () => root.render(
      <QueryClientProvider client={client}><RegenerateListingButton convId={convId} /></QueryClientProvider>,
    ));
    const button = container.querySelector("button")!;
    await act(async () => { await vi.waitFor(() => expect(button.disabled).toBe(false)); });
    await act(async () => button.click());
    expect(getLiveListings).not.toHaveBeenCalled();
    await act(async () => respondToConfirmDialog(true));
    expect(getLiveListings).toHaveBeenCalledTimes(1);
    expect(readConfirmDialogState().status).toBe("closing");
    await act(async () => completeConfirmDialogClose());
    expect(readConfirmDialogState()).toMatchObject({ status: "confirming", confirmLabel: "Delist from eBay" });
    await act(async () => respondToConfirmDialog(true));
    expect(reset).toHaveBeenCalledWith(convId, { keepInputs: true });
    expect(holdRewrite(convId)).toBe(false);
    expect(waitlist.add).not.toHaveBeenCalled();

    act(() => root.unmount());
    mounted = false;
    await act(async () => finishReset());

    expect(toastManager.add).toHaveBeenCalledWith(expect.objectContaining({ title: "Could not regenerate listing" }));
    if (failure === "reset") expect(waitlist.add).not.toHaveBeenCalled();
    else expect(waitlist.add).toHaveBeenCalledExactlyOnceWith({
      convId, title: "Wool coat", marketplace: "ebay", kind: "delist", revisionId: "",
    });
    expect(holdRewrite(convId)).toBe(true);
  } finally {
    if (mounted) act(() => root.unmount());
    releaseRewrite(convId);
    unregister();
    completeConfirmDialogClose();
    client.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});
