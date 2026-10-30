import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { createStudioQueryClient } from "./queryClient";
import { listingEdits } from "./listingEdits";
import type { ListingData, ListingResponse, ListingUpdateResult } from "./types";

afterEach(() => vi.restoreAllMocks());

function setup() {
  const client = createStudioQueryClient();
  client.setQueryData<ListingResponse>(["listing", "item"], {
    conversation_id: "item", current_revision_id: "r0", revision_count: 1,
    listing: { title: "Original", price: 10 }, can_send: false, errors: [], warnings: [],
  });
  return { client, edits: listingEdits(client, "item") };
}
function result(revision: string, listing: ListingData): ListingUpdateResult {
  return { ok: true, revision_id: revision, listing, etsy_eligible: false,
    validation: { valid: true, can_send: true, errors: [], warnings: [], info: [] } };
}

it("keeps editing during a slow save, coalesces edits, and rebases them onto the server response", async () => {
  const { client, edits } = setup();
  let resolveFirst!: (value: ListingUpdateResult) => void;
  const first = new Promise<ListingUpdateResult>(resolve => { resolveFirst = resolve; });
  const save = vi.spyOn(api.listings, "update")
    .mockReturnValueOnce(first)
    .mockImplementationOnce(async (_id, listing) => result("r2", listing));
  try {
    edits.save({ title: "First", price: 10 });
    edits.save({ title: "First", price: 20 });
    edits.save({ title: "First", price: 30 });
    expect(edits.getSnapshot().draft?.price).toBe(30);
    expect(edits.getSnapshot().saving).toBe(true);
    expect(save).toHaveBeenCalledTimes(1);
    resolveFirst(result("r1", { title: "Normalized first", price: 10, brand: "Nike" }));
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    expect(save).toHaveBeenNthCalledWith(2, "item", { title: "Normalized first", price: 30, brand: "Nike" }, "r1");
    expect(client.getQueryData<ListingResponse>(["listing", "item"])?.listing.price).toBe(30);
    expect(edits.getSnapshot().draft).toBeNull();
  } finally { client.clear(); }
});

it("preserves failed edits across remounts and never retries against an external revision", async () => {
  const { client, edits } = setup();
  const save = vi.spyOn(api.listings, "update").mockRejectedValueOnce(new Error("This listing changed."));
  try {
    edits.save({ title: "My edit", price: 10 });
    await vi.waitFor(() => expect(edits.getSnapshot().error).toBe("This listing changed."));
    edits.save({ title: "My newest edit", price: 20 });
    expect(save).toHaveBeenCalledTimes(1);
    expect(listingEdits(client, "item").getSnapshot().draft?.price).toBe(20);
    client.setQueryData<ListingResponse>(["listing", "item"], old => ({ ...old!, current_revision_id: "someone-else" }));
    save.mockRejectedValueOnce(new Error("This listing changed."));
    edits.retry();
    await vi.waitFor(() => expect(edits.getSnapshot().error).toBe("This listing changed."));
    expect(save).toHaveBeenLastCalledWith("item", { title: "My newest edit", price: 20 }, "r0");
    edits.discard();
    expect(edits.getSnapshot().draft).toBeNull();
  } finally { client.clear(); }
});

it("retries a temporary failure with the retained draft", async () => {
  const { client, edits } = setup();
  vi.spyOn(api.listings, "update").mockRejectedValueOnce(new Error("Offline"))
    .mockImplementationOnce(async (_id, listing) => result("r1", listing));
  try {
    edits.save({ title: "Kept edit" });
    await vi.waitFor(() => expect(edits.getSnapshot().error).toBe("Offline"));
    edits.retry();
    await vi.waitFor(() => expect(edits.getSnapshot().draft).toBeNull());
    expect(client.getQueryData<ListingResponse>(["listing", "item"])?.listing.title).toBe("Kept edit");
  } finally { client.clear(); }
});

it("keeps explicitly cleared choices when a refresh returns older saved values", async () => {
  const { client, edits } = setup();
  vi.spyOn(api.listings, "update").mockRejectedValue(new Error("Offline"));
  try {
    edits.save({ title: "Original", depop_specifics: { style: [] } });
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    client.setQueryData<ListingResponse>(["listing", "item"], old => ({ ...old!,
      listing: { title: "Original", depop_specifics: { style: ["Casual"] } },
    }));
    expect(edits.getSnapshot().draft?.depop_specifics).toEqual({ style: [] });
  } finally { client.clear(); }
});
