// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ASSISTANT_MEMORY_KEY, ASSISTANT_NAME_KEY, ASSISTANT_NOTE_KEY } from "../../api/assistant";
import {
  completeConfirmDialogClose,
  readConfirmDialogState,
  registerConfirmDialogHost,
  respondToConfirmDialog,
} from "../../ui/confirmDialog";
import { AssistantSettings } from "./AssistantSettings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  completeConfirmDialogClose();
  vi.restoreAllMocks();
});

function mountAssistant(memories: Array<{ id: string; text: string; created_at: string; updated_at: string }>) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  client.setQueryData(ASSISTANT_NAME_KEY, { name: "" });
  client.setQueryData(ASSISTANT_NOTE_KEY, { note: "" });
  client.setQueryData(ASSISTANT_MEMORY_KEY, { memories });

  const unregister = registerConfirmDialogHost();
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <AssistantSettings />
      </QueryClientProvider>,
    );
  });
  return {
    host,
    client,
    unmount: async () => {
      await act(async () => root.unmount());
      unregister();
      client.clear();
      host.remove();
    },
  };
}

describe("AssistantSettings memory removal", () => {
  it("asks to confirm before forgetting a memory and leaves it intact when cancelled", async () => {
    const forget = vi.spyOn(api.assistant, "forget").mockResolvedValue({ memories: [] });
    const view = mountAssistant([
      { id: "mem-1", text: "Ships from Austin", created_at: "2026-11-20T00:00:00Z", updated_at: "2026-11-20T00:00:00Z" },
    ]);
    try {
      const removeButton = [...view.host.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Remove",
      );
      await act(async () => {
        removeButton!.click();
      });

      expect(forget).not.toHaveBeenCalled();
      const dialog = readConfirmDialogState();
      expect(dialog.status).toBe("confirming");
      if (dialog.status === "confirming") {
        expect(dialog.message).toContain("Forget this memory?");
        expect(dialog.message).toContain("Ships from Austin");
        expect(dialog.confirmLabel).toBe("Remove");
        expect(dialog.variant).toBe("destructive");
      }

      await act(async () => {
        respondToConfirmDialog(false);
        completeConfirmDialogClose();
      });

      expect(forget).not.toHaveBeenCalled();
      expect(view.host.textContent).toContain("Ships from Austin");
    } finally {
      await view.unmount();
    }
  });

  it("forgets immediately on the server once confirmed", async () => {
    const forget = vi.spyOn(api.assistant, "forget").mockResolvedValue({ memories: [] });
    const view = mountAssistant([
      { id: "mem-1", text: "Ships from Austin", created_at: "2026-11-20T00:00:00Z", updated_at: "2026-11-20T00:00:00Z" },
    ]);
    try {
      const removeButton = [...view.host.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Remove",
      );
      await act(async () => {
        removeButton!.click();
      });
      expect(forget).not.toHaveBeenCalled();

      await act(async () => {
        respondToConfirmDialog(true);
        completeConfirmDialogClose();
        await Promise.resolve();
        await Promise.resolve();
      });

      await vi.waitFor(() => {
        expect(forget).toHaveBeenCalledWith("mem-1");
        expect(view.host.textContent).not.toContain("Ships from Austin");
      });
    } finally {
      await view.unmount();
    }
  });
});
