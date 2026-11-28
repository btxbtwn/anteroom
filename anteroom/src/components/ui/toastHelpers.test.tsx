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
  vi.restoreAllMocks();
});

describe("showUndoToast", () => {
  it("renders a done · Undo toast with the default timeout and runs onUndo once when clicked", async () => {
    const view = mountToasts();
    const addSpy = vi.spyOn(toastManager, "add");
    const onUndo = vi.fn();
    try {
      await act(async () => {
        showUndoToast({ title: "Removed preset", onUndo });
      });
      expect(addSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Removed preset",
          timeout: UNDO_TOAST_TIMEOUT_MS,
        }),
      );
      const undoButton = [...document.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Undo",
      );
      expect(document.body.textContent).toContain("Removed preset");
      expect(undoButton).toBeDefined();

      await act(async () => {
        undoButton!.click();
        undoButton!.click();
      });

      expect(onUndo).toHaveBeenCalledTimes(1);
    } finally {
      view.unmount();
    }
  });

  it("dismisses without running onUndo when the corner close button is clicked", async () => {
    const view = mountToasts();
    const onUndo = vi.fn();
    try {
      await act(async () => {
        showUndoToast({ title: "Removed preset", onUndo });
      });
      const closeButton = document.querySelector<HTMLButtonElement>('[aria-label="Dismiss notification"]');
      expect(closeButton).not.toBeNull();

      await act(async () => {
        closeButton!.click();
      });

      expect(onUndo).not.toHaveBeenCalled();
    } finally {
      view.unmount();
    }
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
