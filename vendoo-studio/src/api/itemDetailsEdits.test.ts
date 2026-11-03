import { afterEach, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { itemDetailsEdits } from "./itemDetailsEdits";

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.spyOn(api.settings, "packageDimensions").mockResolvedValue({ length: 15, width: 12, height: 4 } as never);
  let notes = JSON.stringify({ sellerNotes: "Original", sku: "KEEP", knownFlaws: "Mark", categoryOverride: "Shirts" });
  vi.spyOn(api.conversations, "get").mockImplementation(async id => ({ id, notes }) as never);
  const update = vi.spyOn(api.conversations, "update").mockImplementation(async (id, body) => {
    notes = body.notes!;
    return { id, notes } as never;
  });
  return { client, update };
}

it("finishes a pending save after the editor unsubscribes without losing untouched fields", async () => {
  vi.useFakeTimers();
  const { client, update } = setup();
  const edits = itemDetailsEdits(client, "first");
  const unsubscribe = edits.subscribe(() => {});
  edits.change({ sellerNotes: "Typed before loading" });
  unsubscribe();
  await vi.advanceTimersByTimeAsync(500);
  expect(update).toHaveBeenCalledOnce();
  expect(JSON.parse(update.mock.calls[0][1].notes!)).toMatchObject({ sellerNotes: "Typed before loading", sku: "KEEP", knownFlaws: "Mark", categoryOverride: "Shirts", packageDimensions: "15x12x4" });
  expect(itemDetailsEdits(client, "first")).toBe(edits);
  expect(edits.getSnapshot()).toEqual({ patch: null, saving: false, error: null });
  client.clear();
});

it("combines separate measurement edits before the debounce completes", async () => {
  vi.useFakeTimers();
  const { client, update } = setup();
  const edits = itemDetailsEdits(client, "first");
  edits.change({ measurements: { top: { pitToPit: "22" }, pants: {}, skirt: {} } });
  edits.change({ measurements: { top: { length: "27" }, pants: {}, skirt: {} } });
  await vi.advanceTimersByTimeAsync(500);
  expect(JSON.parse(update.mock.calls[0][1].notes!).measurements.top).toEqual({ pitToPit: "22", length: "27" });
  client.clear();
});

it("keeps newer typing while an earlier save is in flight", async () => {
  const { client, update } = setup();
  let finish!: (response: never) => void;
  update.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const edits = itemDetailsEdits(client, "first");
  edits.change({ sellerNotes: "First version" }, true);
  await vi.waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  edits.change({ sellerNotes: "Newer version" }, true);
  finish({ id: "first", notes: update.mock.calls[0][1].notes } as never);
  await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
  expect(update).toHaveBeenCalledTimes(2);
  expect(JSON.parse(update.mock.calls[1][1].notes!).sellerNotes).toBe("Newer version");
  expect(edits.getSnapshot().patch).toBeNull();
  client.clear();
});

it("retains failed edits for retry and keeps other drafts independent", async () => {
  const { client, update } = setup();
  update.mockRejectedValueOnce(new Error("Temporary failure"));
  const first = itemDetailsEdits(client, "first");
  first.change({ sellerNotes: "Keep this edit" }, true);
  await vi.waitFor(() => expect(first.getSnapshot().error).toBe("Temporary failure"));
  expect(first.getSnapshot().patch).toEqual({ sellerNotes: "Keep this edit" });
  const second = itemDetailsEdits(client, "second");
  second.change({ sellerNotes: "Other draft" }, true);
  await vi.waitFor(() => expect(second.getSnapshot().saving).toBe(false));
  expect(first.getSnapshot().patch?.sellerNotes).toBe("Keep this edit");
  first.retry();
  await vi.waitFor(() => expect(first.getSnapshot()).toEqual({ patch: null, saving: false, error: null }));
  expect(update.mock.calls.map(call => call[0])).toEqual(["first", "second", "first"]);
  client.clear();
});
