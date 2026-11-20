// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NativeDrafts } from "../api/nativeMarketplaces";
import { MarketplacesSection } from "./MarketplacesSection";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { useQuery, useMutation, invalidateQueries } = vi.hoisted(() => ({
  useQuery: vi.fn(),
  useMutation: vi.fn(),
  invalidateQueries: vi.fn(),
}));

vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQuery,
  useMutation,
  useQueryClient: () => ({ invalidateQueries, setQueryData: vi.fn() }),
}));

function mockQueries(drafts: NativeDrafts) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) =>
    queryKey[0] === "native-marketplaces"
      ? { data: drafts, error: null, isError: false, isPending: false }
      : queryKey[0] === "marketplace-listings"
        ? { data: {}, error: null, isPending: false }
        : queryKey[0] === "etsy-listing-defaults"
          ? { data: {}, isSuccess: true, isPending: false }
          : queryKey[0] === "native-preflight"
            ? { data: { marketplaces: {} }, isPending: false, isPlaceholderData: false, isFetching: false, isError: false }
            : { data: { shipping: [], processing: [] }, isPending: false, error: null },
  );
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: null, variables: undefined });
}

describe("MarketplacesSection lifecycle effects", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("invalidates native status only when revisionId changes on the same listing, not on unrelated re-renders or item switches", async () => {
    mockQueries({});
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(() => root.render(
        <MarketplacesSection convId="item-1" revisionId="rev-1" title="Tee" disabled={false} etsyEligible selected="ebay" />,
      ));
      expect(invalidateQueries).not.toHaveBeenCalled();

      // Unrelated prop change (title) should not trigger invalidation.
      await act(() => root.render(
        <MarketplacesSection convId="item-1" revisionId="rev-1" title="Tee v2" disabled={false} etsyEligible selected="ebay" />,
      ));
      expect(invalidateQueries).not.toHaveBeenCalled();

      // Switching to another listing with a different revisionId should not spuriously invalidate.
      await act(() => root.render(
        <MarketplacesSection convId="item-2" revisionId="rev-9" title="Jacket" disabled={false} etsyEligible selected="ebay" />,
      ));
      expect(invalidateQueries).not.toHaveBeenCalled();

      // Saving a new revision on the same listing invalidates its native marketplace status once.
      await act(() => root.render(
        <MarketplacesSection convId="item-2" revisionId="rev-10" title="Jacket" disabled={false} etsyEligible selected="ebay" />,
      ));
      expect(invalidateQueries).toHaveBeenCalledTimes(1);
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["native-marketplaces", "item-2"] });
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });

  it("invalidates live listings and jobs when a running action finishes on the same listing, not when switching listings", async () => {
    const runningDrafts: NativeDrafts = { ebay: { state: "saving", running: true, revision_id: "rev-1" } };
    mockQueries(runningDrafts);
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(() => root.render(
        <MarketplacesSection convId="item-1" revisionId="rev-1" title="Tee" disabled={false} etsyEligible selected="ebay" />,
      ));
      expect(invalidateQueries).not.toHaveBeenCalled();

      // Switching to an idle listing while item-1 was running must not invalidate item-2.
      mockQueries({});
      await act(() => root.render(
        <MarketplacesSection convId="item-2" revisionId="rev-2" title="Hat" disabled={false} etsyEligible selected="ebay" />,
      ));
      expect(invalidateQueries).not.toHaveBeenCalled();

      // Start running on item-2, then finish on item-2 -> invalidates live listings + jobs once.
      mockQueries({ mercari: { state: "saving", running: true, revision_id: "rev-2" } });
      await act(() => root.render(
        <MarketplacesSection convId="item-2" revisionId="rev-2" title="Hat" disabled={false} etsyEligible selected="mercari" />,
      ));
      expect(invalidateQueries).not.toHaveBeenCalled();

      mockQueries({ mercari: { state: "draft", running: false, revision_id: "rev-2" } });
      await act(() => root.render(
        <MarketplacesSection convId="item-2" revisionId="rev-2" title="Hat" disabled={false} etsyEligible selected="mercari" />,
      ));
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["marketplace-listings", "item-2"] });
      expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["jobs", "item-2"] });
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });
});
