// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { toastManager } from "../ui/toast";
import { ListingRow } from "./ListingRow";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../ListingTemplatesDialog", () => ({ ListingTemplatesDialog: () => null }));
vi.mock("../CopyListingDialog", () => ({ CopyListingDialog: () => null }));

describe("ListingRow rename error feedback", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows an error toast and keeps the rename input open when renaming fails", async () => {
    vi.spyOn(api.conversations, "update").mockRejectedValue(new Error("Server unreachable"));
    const toastSpy = vi.spyOn(toastManager, "add").mockReturnValue("toast-1");

    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(() => root.render(
        <QueryClientProvider client={client}>
          <ListingRow
            listing={{ id: "item-1", title: "Vintage Jacket", status: "draft" }}
            selected
            settled={false}
            onSelect={vi.fn()}
            onDelete={vi.fn()}
          />
        </QueryClientProvider>,
      ));

      const renameButton = container.querySelector('button[aria-label="Rename Vintage Jacket"]') as HTMLButtonElement | null;
      expect(renameButton).not.toBeNull();
      await act(() => {
        renameButton!.click();
      });

      const input = container.querySelector('input[aria-label="Rename Vintage Jacket"]') as HTMLInputElement | null;
      expect(input).not.toBeNull();
      await act(() => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
        setter.call(input!, "Wool Chore Coat");
        input!.dispatchEvent(new Event("input", { bubbles: true }));
      });

      await act(async () => {
        input!.blur();
        await Promise.resolve();
      });

      expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({
        type: "error",
        title: "Could not rename the listing",
        description: "Server unreachable",
      }));
      // Input stays open with the seller's typed value preserved.
      const remainingInput = container.querySelector('input[aria-label="Rename Vintage Jacket"]') as HTMLInputElement | null;
      expect(remainingInput?.value).toBe("Wool Chore Coat");
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });
});
