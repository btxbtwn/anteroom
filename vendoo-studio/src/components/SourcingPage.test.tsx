// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourcingPage } from "./SourcingPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: undefined, isLoading: true, isError: false, error: null }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ setQueryData: vi.fn(), invalidateQueries: vi.fn() }),
}));

vi.mock("./ScoutPanel", () => ({
  ScoutPanel: () => <div data-testid="scout-panel">Scout panel</div>,
}));

vi.mock("./BoughtBoxes", () => ({
  BoughtBoxes: () => null,
}));

describe("SourcingPage storage guard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders and switches tabs even when localStorage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage unavailable", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    });

    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(() => root.render(<SourcingPage onOpenListing={vi.fn()} />));
      expect(container.textContent).toContain("Wholesale boxes");
      expect(container.querySelector('[data-testid="scout-panel"]')).toBeNull();

      const scoutTab = [...container.querySelectorAll('[role="tab"]')].find(
        (el) => el.textContent?.includes("Scout an item"),
      ) as HTMLElement | undefined;
      expect(scoutTab).toBeDefined();

      await act(() => {
        scoutTab!.click();
      });
      expect(container.querySelector('[data-testid="scout-panel"]')).not.toBeNull();
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });
});
