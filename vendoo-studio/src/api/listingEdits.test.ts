import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { createStudioQueryClient } from "./queryClient";
import { listingEdits } from "./listingEdits";
import type { ListingData, ListingResponse, ListingUpdateResult } from "./types";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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

it("recovers from an HTTP 409 through the listing API without overwriting a newer price", async () => {
  const { client, edits } = setup();
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "This listing changed. Reload it before saving your edits." }), { status: 409 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      ...client.getQueryData<ListingResponse>(["listing", "item"])!,
      current_revision_id: "r1", listing: { title: "Original", price: 25 },
    })))
    .mockResolvedValueOnce(new Response(JSON.stringify(result("r2", { title: "Edited", price: 25 }))));
  vi.stubGlobal("fetch", fetch);
  try {
    edits.save({ title: "Edited", price: 10 });
    await vi.waitFor(() => expect(edits.getSnapshot().error).toContain("This listing changed"));
    edits.retry();
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(fetch.mock.calls[1][0]).toBe("/api/conversations/item/listing");
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({
      listing: { title: "Edited", price: 25 }, expected_revision_id: "r1",
    });
    expect(edits.getSnapshot()).toEqual({ draft: null, saving: false, error: null });
    expect(client.getQueryData<ListingResponse>(["listing", "item"])?.listing).toEqual({ title: "Edited", price: 25 });
  } finally { client.clear(); }
});

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

it("preserves failed edits across remounts and merges separate changes on retry", async () => {
  const { client, edits } = setup();
  const save = vi.spyOn(api.listings, "update").mockRejectedValueOnce(new Error("This listing changed."));
  try {
    edits.save({ title: "My edit", price: 10 });
    await vi.waitFor(() => expect(edits.getSnapshot().error).toBe("This listing changed."));
    edits.save({ title: "My newest edit", price: 20 });
    expect(save).toHaveBeenCalledTimes(1);
    expect(listingEdits(client, "item").getSnapshot().draft?.price).toBe(20);
    client.setQueryData<ListingResponse>(["listing", "item"], old => ({ ...old!, current_revision_id: "someone-else" }));
    vi.spyOn(api.listings, "get").mockResolvedValue({
      ...client.getQueryData<ListingResponse>(["listing", "item"])!,
      listing: { title: "Original", price: 10, brand: "Nike" },
    });
    save.mockImplementationOnce(async (_id, listing) => result("r2", listing));
    edits.retry();
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    expect(save).toHaveBeenLastCalledWith("item", { title: "My newest edit", price: 20, brand: "Nike" }, "someone-else");
    expect(edits.getSnapshot().error).toBeNull();
    expect(edits.getSnapshot().draft).toBeNull();
  } finally { client.clear(); }
});

it("retries a temporary failure with the retained draft", async () => {
  const { client, edits } = setup();
  vi.spyOn(api.listings, "get").mockResolvedValue(client.getQueryData<ListingResponse>(["listing", "item"])!);
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

it("keeps edits and reports the field when both copies changed it", async () => {
  const { client, edits } = setup();
  const save = vi.spyOn(api.listings, "update").mockRejectedValue(new Error("This listing changed."));
  vi.spyOn(api.listings, "get").mockResolvedValue({
    ...client.getQueryData<ListingResponse>(["listing", "item"])!,
    current_revision_id: "external", listing: { title: "Someone else's title", price: 10 },
  });
  try {
    edits.save({ title: "My title", price: 10 });
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    edits.retry();
    await vi.waitFor(() => expect(edits.getSnapshot().error).toContain("field title also changed"));
    expect(save).toHaveBeenCalledTimes(1);
    expect(edits.getSnapshot().draft?.title).toBe("My title");
  } finally { client.clear(); }
});

it("merges nested edits, cleared arrays and removed fields without losing external changes", async () => {
  const { client, edits } = setup();
  const original = { title: "Original", brand: "Old", depop_specifics: { style: ["Casual"], material: "Cotton" } };
  client.setQueryData<ListingResponse>(["listing", "item"], old => ({ ...old!, listing: original }));
  const save = vi.spyOn(api.listings, "update").mockRejectedValueOnce(new Error("Conflict"))
    .mockImplementationOnce(async (_id, listing) => result("r2", listing));
  vi.spyOn(api.listings, "get").mockResolvedValue({
    ...client.getQueryData<ListingResponse>(["listing", "item"])!, current_revision_id: "r1",
    listing: { ...original, depop_specifics: { style: ["Casual"], material: "Linen" } },
  });
  try {
    edits.save({ title: "Original", depop_specifics: { style: [], material: "Cotton" } });
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    edits.retry();
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    expect(save).toHaveBeenLastCalledWith("item", { title: "Original", depop_specifics: { style: [], material: "Linen" } }, "r1");
    expect(edits.getSnapshot().error).toBeNull();
  } finally { client.clear(); }
});

it("includes edits made while retry fetches the latest listing and prevents duplicate retries", async () => {
  const { client, edits } = setup();
  let resolve!: (value: ListingResponse) => void;
  const get = vi.spyOn(api.listings, "get").mockReturnValue(new Promise(done => { resolve = done; }));
  const save = vi.spyOn(api.listings, "update").mockRejectedValueOnce(new Error("Conflict"))
    .mockImplementationOnce(async (_id, listing) => result("r2", listing));
  try {
    edits.save({ title: "First", price: 10 });
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    edits.retry();
    edits.retry();
    edits.save({ title: "Newest", price: 10 });
    resolve({ ...client.getQueryData<ListingResponse>(["listing", "item"])!, current_revision_id: "r1", listing: { title: "Original", price: 20 } });
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    expect(get).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenLastCalledWith("item", { title: "Newest", price: 20 }, "r1");
  } finally { client.clear(); }
});

it("retains the draft if refreshing for retry fails", async () => {
  const { client, edits } = setup();
  const save = vi.spyOn(api.listings, "update").mockRejectedValue(new Error("Conflict"));
  vi.spyOn(api.listings, "get").mockRejectedValue(new Error("Offline"));
  try {
    edits.save({ title: "Kept" });
    await vi.waitFor(() => expect(edits.getSnapshot().saving).toBe(false));
    edits.retry();
    await vi.waitFor(() => expect(edits.getSnapshot().error).toBe("Offline"));
    expect(edits.getSnapshot().draft?.title).toBe("Kept");
    expect(save).toHaveBeenCalledTimes(1);
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
