import { afterEach, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { itemDetailsEdits } from "./itemDetailsEdits";
import { commitUnsavedWork, hasUnsavedWork } from "../lib/unsavedWork";

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.spyOn(api.settings, "packageDimensions").mockResolvedValue({ length: 15, width: 12, height: 4 } as never);
  let notes = JSON.stringify({ sellerNotes: "Original", sku: "KEEP", knownFlaws: "Mark", categoryOverride: "Shirts" });
  vi.spyOn(api.conversations, "get").mockImplementation(async id => ({ id, notes }) as never);
  // The server merges the changed keys into the notes as they are now.
  const update = vi.spyOn(api.conversations, "update").mockImplementation(async (id, body) => {
    const stored = JSON.parse(notes) as Record<string, unknown>;
    const changes = body.note_changes!;
    const measurements = changes.measurements as Record<string, object> | undefined;
    const storedMeasurements = (stored.measurements ?? {}) as Record<string, object>;
    notes = JSON.stringify({ ...stored, ...changes, ...(measurements ? { measurements: Object.fromEntries(Object.entries({
      ...storedMeasurements, ...measurements }).map(([garment, fields]) => [garment, { ...storedMeasurements[garment], ...fields }])) } : {}) });
    return { id, notes } as never;
  });
  const serverWrites = (changes: Record<string, unknown>) => { notes = JSON.stringify({ ...JSON.parse(notes), ...changes }); };
  return { client, update, serverWrites, notes: () => JSON.parse(notes) as Record<string, unknown> };
}

it("sends only the changed keys, so a key the server writes during the save survives", async () => {
  const { client, update, serverWrites, notes } = setup();
  vi.spyOn(api.conversations, "get").mockImplementation(async id => {
    const read = { id, notes: JSON.stringify(notes()) };
    // A category lookup finishes between the editor's read and its write.
    serverWrites({ categoryOverride: "Tops", sku: "SERVER" });
    return read as never;
  });
  const edits = itemDetailsEdits(client, "first");
  edits.change({ knownFlaws: "Pilling" }, true);
  await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
  const sent = update.mock.calls[0][1];
  expect(sent.notes).toBeUndefined();
  expect(sent.note_changes).not.toHaveProperty("sku");
  expect(sent.note_changes).not.toHaveProperty("sellerNotes");
  expect(sent.note_changes).toMatchObject({ knownFlaws: "Pilling", packageDimensions: "15x12x4" });
  expect(notes()).toMatchObject({ categoryOverride: "Tops", sku: "SERVER", knownFlaws: "Pilling", sellerNotes: "Original" });
  client.clear();
});

it("counts debounced and in-flight details as unsaved work, and saves them at once on the way out", async () => {
  vi.useFakeTimers();
  const { client, update } = setup();
  const edits = itemDetailsEdits(client, "leaving");
  edits.change({ sellerNotes: "Typed just before reload" });
  expect(hasUnsavedWork()).toBe(true);
  commitUnsavedWork();
  await vi.advanceTimersByTimeAsync(0);
  expect(update).toHaveBeenCalledOnce();
  await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
  expect(hasUnsavedWork()).toBe(false);
  client.clear();
});

it("finishes a pending save after the editor unsubscribes without losing untouched fields", async () => {
  vi.useFakeTimers();
  const { client, update, notes } = setup();
  const edits = itemDetailsEdits(client, "first");
  const unsubscribe = edits.subscribe(() => {});
  edits.change({ sellerNotes: "Typed before loading" });
  unsubscribe();
  await vi.advanceTimersByTimeAsync(500);
  expect(update).toHaveBeenCalledOnce();
  expect(notes()).toMatchObject({ sellerNotes: "Typed before loading", sku: "KEEP", knownFlaws: "Mark", categoryOverride: "Shirts", packageDimensions: "15x12x4" });
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
  expect(update.mock.calls[0][1].note_changes!.measurements).toMatchObject({ top: { pitToPit: "22", length: "27" } });
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
  finish({ id: "first", notes: "{}" } as never);
  await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
  expect(update).toHaveBeenCalledTimes(2);
  expect(update.mock.calls[1][1].note_changes!.sellerNotes).toBe("Newer version");
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
