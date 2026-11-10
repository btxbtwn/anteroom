// @vitest-environment happy-dom
import { act, useLayoutEffect } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { api } from "../api/client";
import { followListingGeneration } from "../bulkRegenerate";
import { useBulkRegenerate, type BulkRegenerate } from "./useBulkRegenerate";

vi.mock("../bulkRegenerate", async (original) => ({
  ...await original<typeof import("../bulkRegenerate")>(),
  followListingGeneration: vi.fn(),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it("starts all five rewrites while editor refreshes remain pending", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const root = createRoot(document.createElement("div"));
  const ids = Array.from({ length: 5 }, (_, index) => `bulk-refresh-${index}`);
  let bulk!: BulkRegenerate;
  function Harness() {
    const value = useBulkRegenerate(() => false);
    useLayoutEffect(() => { bulk = value; });
    return null;
  }
  const reset = vi.spyOn(api.conversations, "reset").mockResolvedValue({} as Awaited<ReturnType<typeof api.conversations.reset>>);
  let releaseReads!: () => void;
  const reads = new Promise<void>((resolve) => { releaseReads = resolve; });
  vi.spyOn(api.listings, "get").mockImplementation(async () => {
    await reads;
    return {} as Awaited<ReturnType<typeof api.listings.get>>;
  });
  vi.spyOn(api.conversations, "messages").mockImplementation(async () => { await reads; return []; });
  vi.spyOn(api.conversations, "activity").mockResolvedValue({ busy: false } as Awaited<ReturnType<typeof api.conversations.activity>>);
  // An active editor's invalidation can wait on a slow screen read too.
  vi.spyOn(client, "invalidateQueries").mockImplementation(async () => { await reads; });
  vi.mocked(followListingGeneration).mockResolvedValue();
  let run: ReturnType<BulkRegenerate["start"]> | undefined;
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>); });
    await act(async () => {
      run = bulk.start(ids, new Map(ids.map((id) => [id, id])));
      for (let step = 0; step < 20; step++) await Promise.resolve();
    });
    expect(reset).toHaveBeenCalledTimes(5);
    expect(vi.mocked(followListingGeneration).mock.calls.map(([id]) => id).sort()).toEqual(ids);
  } finally {
    releaseReads();
    await act(async () => { await run; root.unmount(); });
    client.clear();
    vi.restoreAllMocks();
    vi.mocked(followListingGeneration).mockReset();
  }
});
