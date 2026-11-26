// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { NativeDrafts, PreflightGap } from "../api/nativeMarketplaces";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";
import { MarketplacesLayout, MarketplacesSection, useReportFixTargets } from "./MarketplacesSection";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { useQuery, useMutation, invalidateQueries, getQueryData } = vi.hoisted(() => ({
  useQuery: vi.fn(),
  useMutation: vi.fn(),
  invalidateQueries: vi.fn(),
  getQueryData: vi.fn(),
}));

vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQuery,
  useMutation,
  useQueryClient: () => ({ getQueryData, invalidateQueries, setQueryData: vi.fn() }),
}));

function mockQueries(drafts: NativeDrafts, gaps: PreflightGap[] = []) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) =>
    queryKey[0] === "native-marketplaces"
      ? { data: drafts, error: null, isError: false, isPending: false }
      : queryKey[0] === "marketplace-listings"
        ? { data: {}, error: null, isPending: false }
        : queryKey[0] === "etsy-listing-defaults"
          ? { data: {}, isSuccess: true, isPending: false }
          : queryKey[0] === "native-preflight"
            ? { data: { marketplaces: { depop: { gaps, missing: [] } } }, isPending: false, isPlaceholderData: false, isFetching: false, isError: false }
            : { data: { shipping: [], processing: [] }, isPending: false, error: null },
  );
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: null, variables: undefined });
}

describe("MarketplacesSection lifecycle effects", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reconnects Depop listing actions in Chrome even with a saved Cloud sign-in", async () => {
    mockQueries({ depop: { state: "not_saved", error: "Depop asked to sign in again.", error_code: "signed_out" } });
    getQueryData.mockReturnValueOnce({
      chrome_connected: true,
      sessions: { depop: { state: "signed_in", mode: "cloud", cloud_available: true } },
    });
    const chrome = vi.spyOn(api.marketplaceSessions, "connect").mockResolvedValue({ ok: true, signed_in: true });
    const cloud = vi.spyOn(api.cloudSessions, "connectToken").mockRejectedValue(new Error("Unexpected Cloud reconnect"));
    let action: Promise<unknown> | undefined;
    useMutation.mockImplementation(({ mutationFn }: { mutationFn: (variables: unknown) => Promise<unknown> }) => ({
      mutate: (variables: unknown) => { if (variables) action = mutationFn(variables); },
    }));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(() => root.render(
        <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true, account: { id: "seller", email: "seller@example.test" } }}>
          <MarketplacesSection convId="item" revisionId="rev" title="Tee" disabled={false} etsyEligible selected="depop" />
        </WorkspaceRuntimeProvider>,
      ));
      const button = [...container.querySelectorAll("button")].find(node => node.textContent === "Reconnect Depop");
      expect(button).toBeDefined();
      await act(async () => { button!.click(); await action; });
      expect(chrome).toHaveBeenCalledWith("depop");
      expect(cloud).not.toHaveBeenCalled();
    } finally {
      await act(() => root.unmount());
      chrome.mockRestore();
      cloud.mockRestore();
    }
  });

  it("groups category aliases under one reachable Fix button and removes it when the target unmounts", async () => {
    const message = "Choose a Depop category.";
    mockQueries({ depop: { state: "not_saved", error: message } }, [
      { field: "direct_category_ids.depop", message, blocking: true },
      { field: "marketplace_category_ids.depop", message, blocking: true },
      { field: "listing", message: "Review the listing.", blocking: false },
    ]);
    const fields = [{ key: "direct_category_ids.depop", label: "Category" }];
    function Form() {
      useReportFixTargets("depop", fields);
      return null;
    }
    const onEditField = vi.fn();
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);
    const render = (showForm: boolean) => root.render(
      <MarketplacesLayout convId="item" revisionId="rev" title="Tee" disabled={false} etsyEligible selected="depop"
        ids={["depop"]} onSelect={() => {}} onEditField={onEditField}>
        {showForm && <Form />}
      </MarketplacesLayout>,
    );
    const fixButtons = () => [...container.querySelectorAll("button")].filter((button) => button.textContent?.startsWith("Fix "));
    try {
      await act(() => render(true));
      expect(fixButtons().map((button) => button.textContent)).toEqual(["Fix Category"]);
      expect(container.textContent?.split(message)).toHaveLength(2);
      const [button] = fixButtons();
      expect(document.getElementById(button.getAttribute("aria-describedby")!)?.textContent).toBe(message);
      await act(() => button.click());
      expect(onEditField).toHaveBeenCalledWith("direct_category_ids.depop", "depop");

      await act(() => render(false));
      expect(fixButtons()).toHaveLength(0);
      expect(container.textContent).toContain(message);
      expect(container.textContent).toContain("Review the listing.");
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
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
