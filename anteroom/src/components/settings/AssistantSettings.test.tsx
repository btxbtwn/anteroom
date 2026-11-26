// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { ASSISTANT_MEMORY_KEY, ASSISTANT_NAME_KEY, ASSISTANT_NOTE_KEY } from "../../api/assistant";
import { ToastProvider } from "../ui/toast";
import { AssistantSettings } from "./AssistantSettings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  vi.restoreAllMocks();
});

function mountAssistant(memories: Array<{ id: string; text: string; created_at: string; updated_at: string }>) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  client.setQueryData(ASSISTANT_NAME_KEY, { name: "" });
  client.setQueryData(ASSISTANT_NOTE_KEY, { note: "" });
  client.setQueryData(ASSISTANT_MEMORY_KEY, { memories });

  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <ToastProvider>
          <AssistantSettings />
        </ToastProvider>
      </QueryClientProvider>,
    );
  });
  return {
    host,
    client,
    unmount: async () => {
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    },
  };
}

describe("AssistantSettings memory removal", () => {
  it("hides a removed memory immediately and restores it when Undo is clicked without calling forget", async () => {
    const forget = vi.spyOn(api.assistant, "forget").mockResolvedValue({ memories: [] });
    const view = mountAssistant([
      { id: "mem-1", text: "Ships from Austin", created_at: "2026-11-20T00:00:00Z", updated_at: "2026-11-20T00:00:00Z" },
    ]);
    try {
      expect(view.host.textContent).toContain("Ships from Austin");
      const removeButton = [...view.host.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Remove",
      );
      await act(async () => {
        removeButton!.click();
      });

      expect(view.host.textContent).not.toContain("Ships from Austin");
      expect(forget).not.toHaveBeenCalled();

      const undoButton = [...document.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Undo",
      );
      expect(undoButton).toBeDefined();
      await act(async () => {
        undoButton!.click();
      });

      expect(view.host.textContent).toContain("Ships from Austin");
      expect(forget).not.toHaveBeenCalled();
    } finally {
      await view.unmount();
    }
  });

  it("commits api.assistant.forget when the Undo toast is dismissed", async () => {
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

      const dismissButton = document.querySelector<HTMLButtonElement>('[aria-label="Dismiss notification"]');
      await act(async () => {
        dismissButton!.click();
      });

      await vi.waitFor(() => {
        expect(forget).toHaveBeenCalledWith("mem-1");
      });
    } finally {
      await view.unmount();
    }
  });
});
