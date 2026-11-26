// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, toastManager } from "./toast";
import { UNDO_TOAST_TIMEOUT_MS, showUndoToast } from "./toastHelpers";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function mountToasts() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  act(() => {
    root.render(<ToastProvider><div /></ToastProvider>);
  });
  return {
    unmount: () => {
      act(() => root.unmount());
      host.remove();
    },
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("showUndoToast", () => {
  it("renders a done · Undo toast and runs onUndo without committing when Undo is clicked", async () => {
    const view = mountToasts();
    const onUndo = vi.fn();
    const onCommit = vi.fn();
    try {
      await act(async () => {
        showUndoToast({ title: "Photo removed", onUndo, onCommit });
      });
      const undoButton = [...document.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Undo",
      );
      expect(document.body.textContent).toContain("Photo removed");
      expect(undoButton).toBeDefined();

      await act(async () => {
        undoButton!.click();
      });

      expect(onUndo).toHaveBeenCalledTimes(1);
      expect(onCommit).not.toHaveBeenCalled();
    } finally {
      view.unmount();
    }
  });

  it("commits once when the corner dismiss button is clicked", async () => {
    const view = mountToasts();
    const onUndo = vi.fn();
    const onCommit = vi.fn();
    try {
      await act(async () => {
        showUndoToast({ title: "Memory removed", onUndo, onCommit });
      });
      const closeButton = document.querySelector<HTMLButtonElement>('[aria-label="Dismiss notification"]');
      expect(closeButton).not.toBeNull();

      await act(async () => {
        closeButton!.click();
      });

      expect(onCommit).toHaveBeenCalledTimes(1);
      expect(onUndo).not.toHaveBeenCalled();
    } finally {
      view.unmount();
    }
  });

  it("commits via flush() and ignores subsequent close events", async () => {
    const view = mountToasts();
    const onUndo = vi.fn();
    const onCommit = vi.fn();
    try {
      let handle: ReturnType<typeof showUndoToast>;
      await act(async () => {
        handle = showUndoToast({ title: "Photo removed", onUndo, onCommit });
      });
      await act(async () => {
        handle!.flush();
        handle!.flush();
      });
      expect(onCommit).toHaveBeenCalledTimes(1);
      expect(onUndo).not.toHaveBeenCalled();
    } finally {
      view.unmount();
    }
  });

  it("commits after timeout when no ToastProvider is mounted", () => {
    vi.useFakeTimers();
    const onUndo = vi.fn();
    const onCommit = vi.fn();
    showUndoToast({ title: "Memory removed", onUndo, onCommit });
    expect(onCommit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(UNDO_TOAST_TIMEOUT_MS);
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it("reports a failed async undo with an error toast", async () => {
    const view = mountToasts();
    const addSpy = vi.spyOn(toastManager, "add");
    try {
      await act(async () => {
        showUndoToast({
          title: "Removed preset",
          onUndo: () => Promise.reject(new Error("Server unavailable")),
          undoErrorTitle: "Could not restore preset",
        });
      });
      const undoButton = [...document.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Undo",
      );
      await act(async () => {
        undoButton!.click();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(addSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "error",
          title: "Could not restore preset",
          description: "Server unavailable",
        }),
      );
    } finally {
      view.unmount();
    }
  });
});
