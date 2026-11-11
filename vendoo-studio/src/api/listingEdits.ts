import type { QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { refreshInventory } from "./inventoryRefresh";
import { ApiRequestError } from "./transport";
import type { ConversationSummary, ListingData, ListingDocument } from "./types";
import { listingCategorySignature } from "../components/nativeFormStability";
import { notifyUnsavedWork, registerUnsavedWork } from "../lib/unsavedWork";

/** Keep newer field edits while accepting the server's normalized unchanged fields. */
function rebase(saved: unknown, before: unknown, after: unknown, checkConflicts = false, path = ""): unknown {
  if (JSON.stringify(before) === JSON.stringify(after)) return saved;
  if (JSON.stringify(saved) === JSON.stringify(after)) return saved;
  if (!saved || !before || !after || typeof saved !== "object" || typeof before !== "object" || typeof after !== "object" || Array.isArray(saved) || Array.isArray(before) || Array.isArray(after)) {
    if (checkConflicts && JSON.stringify(saved) !== JSON.stringify(before)) {
      throw new Error(`The field ${path || "listing"} also changed in the saved listing. Your edits are kept. Copy them before using Discard and reload.`);
    }
    return after;
  }
  const result = { ...(saved as Record<string, unknown>) };
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const value = rebase(result[key], (before as Record<string, unknown>)[key], (after as Record<string, unknown>)[key], checkConflicts, path ? `${path}.${key}` : key);
    if (!(key in after) && value === undefined) delete result[key];
    else result[key] = value;
  }
  return result;
}

class ListingEdits {
  private state: { draft: ListingData | null; saving: boolean; error: string | null } = { draft: null, saving: false, error: null };
  private listeners = new Set<() => void>();
  private revisionId = "";
  private base: ListingData = {};
  constructor(
    private client: QueryClient,
    private id: string,
    private registry: { attach: (edits: ListingEdits) => void; release: (edits: ListingEdits) => void },
  ) {}

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    this.registry.attach(this);
    return () => {
      this.listeners.delete(listener);
      this.releaseIfIdle();
    };
  };
  /** A queued draft, a save in flight, or a failed save the seller has not retried or discarded. */
  get pending() {
    return Boolean(this.state.draft) || this.state.saving;
  }
  private update(state: typeof this.state) {
    this.state = state;
    if (this.pending) unsavedQueues.add(this);
    else unsavedQueues.delete(this);
    notifyUnsavedWork();
    this.listeners.forEach(listener => listener());
    this.releaseIfIdle();
  }
  /** Nothing watches it and nothing is left to save: drop it from the map. */
  private releaseIfIdle() {
    // Deferred: React re-subscribes in the same task when it remounts an editor.
    setTimeout(() => {
      if (!this.listeners.size && !this.pending && !this.state.error) this.registry.release(this);
    }, 0);
  }

  save = (listing: ListingData) => {
    if (!this.state.draft) {
      const saved = this.client.getQueryData<ListingDocument>(["listing", this.id]);
      this.revisionId = saved?.current_revision_id ?? "";
      this.base = saved?.listing ?? {};
    }
    void this.client.cancelQueries({ queryKey: ["listing", this.id], exact: true });
    this.update({ ...this.state, draft: listing });
    if (!this.state.saving && !this.state.error) void this.flush();
  };

  retry = () => {
    if (this.state.saving || !this.state.draft) return;
    this.update({ ...this.state, error: null });
    void this.flush(true);
  };

  discard = () => {
    if (this.state.saving) return;
    this.update({ draft: null, saving: false, error: null });
    void this.client.invalidateQueries({ queryKey: ["listing", this.id] });
  };

  private async flush(refresh = false) {
    this.update({ ...this.state, saving: true });
    try {
      if (refresh) {
        const latest = await api.listings.get(this.id);
        const merged = rebase(latest.listing, this.base, this.state.draft, true) as ListingData;
        this.revisionId = latest.current_revision_id ?? "";
        this.base = latest.listing;
        this.update({ ...this.state, draft: merged });
      }
      while (this.state.draft) {
        const submitted = this.state.draft;
        const result = await api.listings.update(this.id, submitted, this.revisionId).catch(async error => {
          if (!(error instanceof ApiRequestError) || error.status !== 409 || error.reason !== "draft_busy") throw error;
          // Keep typing locally until the worker releases this draft, then
          // merge against its result without overwriting concurrent changes.
          while ((await api.conversations.activity(this.id)).busy) {
            await new Promise(resolve => setTimeout(resolve, 1000));
          }
          const latest = await api.listings.get(this.id);
          const merged = rebase(latest.listing, this.base, this.state.draft, true) as ListingData;
          this.revisionId = latest.current_revision_id ?? "";
          this.base = latest.listing;
          this.update({ ...this.state, draft: merged });
          return null;
        });
        if (!result) continue;
        this.revisionId = result.revision_id;
        this.base = result.listing;
        const pending = this.state.draft === submitted ? null : rebase(result.listing, submitted, this.state.draft) as ListingData;
        const previous = this.client.getQueryData<ListingDocument>(["listing", this.id]);
        this.client.setQueryData<ListingDocument>(["listing", this.id], {
          conversation_id: this.id, current_revision_id: result.revision_id, listing: result.listing,
          revision_count: (previous?.revision_count ?? 0) + 1, can_send: result.validation.can_send,
          errors: result.validation.errors, warnings: result.validation.warnings, etsy_eligible: result.etsy_eligible,
        });
        if (listingCategorySignature(previous?.listing) !== listingCategorySignature(result.listing)) {
          void this.client.invalidateQueries({ queryKey: ["listing-fields", this.id] });
        }
        if (!pending) {
          void this.client.invalidateQueries({ queryKey: ["conversation", this.id] });
          // The row shows the saved price at once; the shared schedule picks up the rest.
          const price = result.listing.price;
          if (typeof price === "number") {
            this.client.setQueryData<ConversationSummary[]>(["conversations"], rows =>
              rows?.map(row => row.id === this.id ? { ...row, price } : row));
          }
          refreshInventory(this.client);
        }
        this.update({ draft: pending, saving: Boolean(pending), error: null });
      }
    } catch (error) {
      // Retain the draft and its base so retry can merge without overwriting concurrent edits.
      this.update({ ...this.state, saving: false, error: error instanceof Error ? error.message : "Could not save your edits." });
    }
  }
}

const unsavedQueues = new Set<ListingEdits>();
registerUnsavedWork({ pending: () => unsavedQueues.size > 0 });

const queues = new WeakMap<QueryClient, Map<string, ListingEdits>>();
export function listingEdits(client: QueryClient, id: string) {
  let listings = queues.get(client);
  if (!listings) { listings = new Map(); queues.set(client, listings); }
  let edits = listings.get(id);
  if (!edits) {
    const map = listings;
    edits = new ListingEdits(client, id, {
      attach: (live) => { if (!map.has(id)) map.set(id, live); },
      release: (idle) => { if (map.get(id) === idle) map.delete(id); },
    });
    listings.set(id, edits);
  }
  return edits;
}

/** How many listings hold a save queue; idle queues are dropped. */
export function listingEditQueueCount(client: QueryClient): number {
  return queues.get(client)?.size ?? 0;
}
