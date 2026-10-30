import type { QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { ListingData, ListingResponse } from "./types";
import { listingCategorySignature } from "../components/nativeFormStability";

/** Keep newer field edits while accepting the server's normalized unchanged fields. */
function rebase(saved: unknown, before: unknown, after: unknown): unknown {
  if (JSON.stringify(before) === JSON.stringify(after)) return saved;
  if (!before || !after || typeof before !== "object" || typeof after !== "object" || Array.isArray(before) || Array.isArray(after)) return after;
  const result = { ...(saved as Record<string, unknown>) };
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    if (!(key in after)) delete result[key];
    else result[key] = rebase(result[key], (before as Record<string, unknown>)[key], (after as Record<string, unknown>)[key]);
  }
  return result;
}

class ListingEdits {
  private state: { draft: ListingData | null; saving: boolean; error: string | null } = { draft: null, saving: false, error: null };
  private listeners = new Set<() => void>();
  private revisionId = "";
  constructor(private client: QueryClient, private id: string) {}

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(state: typeof this.state) {
    this.state = state;
    this.listeners.forEach(listener => listener());
  }

  save = (listing: ListingData) => {
    if (!this.state.draft) this.revisionId = this.client.getQueryData<ListingResponse>(["listing", this.id])?.current_revision_id ?? "";
    void this.client.cancelQueries({ queryKey: ["listing", this.id], exact: true });
    this.update({ ...this.state, draft: listing });
    if (!this.state.saving && !this.state.error) void this.flush();
  };

  retry = () => {
    if (this.state.saving || !this.state.draft) return;
    this.update({ ...this.state, error: null });
    void this.flush();
  };

  discard = () => {
    if (this.state.saving) return;
    this.update({ draft: null, saving: false, error: null });
    void this.client.invalidateQueries({ queryKey: ["listing", this.id] });
  };

  private async flush() {
    this.update({ ...this.state, saving: true });
    try {
      while (this.state.draft) {
        const submitted = this.state.draft;
        const result = await api.listings.update(this.id, submitted, this.revisionId);
        this.revisionId = result.revision_id;
        const pending = this.state.draft === submitted ? null : rebase(result.listing, submitted, this.state.draft) as ListingData;
        const previous = this.client.getQueryData<ListingResponse>(["listing", this.id]);
        this.client.setQueryData<ListingResponse>(["listing", this.id], {
          conversation_id: this.id, current_revision_id: result.revision_id, listing: result.listing,
          revision_count: (previous?.revision_count ?? 0) + 1, can_send: result.validation.can_send,
          errors: result.validation.errors, warnings: result.validation.warnings, etsy_eligible: result.etsy_eligible,
        });
        if (listingCategorySignature(previous?.listing) !== listingCategorySignature(result.listing)) {
          void this.client.invalidateQueries({ queryKey: ["listing-fields", this.id] });
        }
        if (!pending) {
          void this.client.invalidateQueries({ queryKey: ["conversation", this.id] });
          void this.client.invalidateQueries({ queryKey: ["conversations"] });
        }
        this.update({ draft: pending, saving: Boolean(pending), error: null });
      }
    } catch (error) {
      // Keep the latest draft and expected revision. A conflict never retries against someone else's revision.
      this.update({ ...this.state, saving: false, error: error instanceof Error ? error.message : "Could not save your edits." });
    }
  }
}

const queues = new WeakMap<QueryClient, Map<string, ListingEdits>>();
export function listingEdits(client: QueryClient, id: string) {
  let listings = queues.get(client);
  if (!listings) { listings = new Map(); queues.set(client, listings); }
  let edits = listings.get(id);
  if (!edits) { edits = new ListingEdits(client, id); listings.set(id, edits); }
  return edits;
}
