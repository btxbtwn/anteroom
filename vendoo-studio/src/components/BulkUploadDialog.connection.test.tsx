// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { api } from "../api/client";
import { BulkUploadDialog } from "./BulkUploadDialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("keeps the same photos and entered details when account relay reading fails", async () => {
  const file = new File(["synthetic"], "coat.jpg", { type: "image/jpeg" });
  const groups = [{ folder: "Coat", files: [file] }];
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const boxes = vi.spyOn(api.boxes, "list").mockResolvedValue({ boxes: [], stores: [] });
  const read = vi.spyOn(api.measurements, "read").mockRejectedValue(new Error("Anteroom could not reach the Mac connection for this account."));
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL() { return "blob:synthetic"; }
    static revokeObjectURL() {}
  });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const confirm = vi.fn(), cancel = vi.fn();
  try {
    await act(async () => root.render(<QueryClientProvider client={client}>
      <BulkUploadDialog groups={groups} onConfirm={confirm} onCancel={cancel} />
    </QueryClientProvider>));
    // The dialog is portaled into document.body, outside the React root.
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog).not.toBeNull();
    const inputs = Array.from(dialog.querySelectorAll<HTMLInputElement>('input[type="text"], input[type="number"]'));
    expect(inputs.length).toBeGreaterThan(0);
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    for (const [index, input] of inputs.entries()) {
      await act(async () => {
        setter.call(input, input.type === "number" ? "12" : `kept-${index}`);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    }
    const values = inputs.map(input => input.value);
    await act(async () => Array.from(dialog.querySelectorAll("button"))
      .find(button => button.textContent?.trim() === "Read measurements")!.click());
    expect(read).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledWith([file], expect.any(String));
    expect(dialog.textContent).toContain("could not reach the Mac connection");
    expect(dialog.textContent).toContain("Your entered details are kept");
    expect(inputs.map(input => input.value)).toEqual(values);
    expect(dialog.isConnected).toBe(true);
    expect(confirm).not.toHaveBeenCalled(); expect(cancel).not.toHaveBeenCalled();
  } finally {
    await act(async () => root.unmount()); client.clear(); host.remove();
    boxes.mockRestore(); read.mockRestore(); vi.unstubAllGlobals();
  }
});
