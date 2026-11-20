import type { QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { detailNoteChanges, mergeMeasurements, parseNotes, type ItemDetailsData } from "../components/itemDetailsData";
import { splitLabels } from "../components/itemLabels";
import { notifyUnsavedWork, registerUnsavedWork } from "../lib/unsavedWork";

class ItemDetailsEdits {
  private state: { patch: Partial<ItemDetailsData> | null; saving: boolean; error: string | null } = { patch: null, saving: false, error: null };
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private flushing = false;
  constructor(private client: QueryClient, private id: string) {}

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  /** Details waiting out the debounce, being saved, or kept after a failed save. */
  get pending() {
    return Boolean(this.state.patch) || this.flushing;
  }
  private update(state: typeof this.state) {
    this.state = state;
    if (this.pending) unsaved.add(this);
    else unsaved.delete(this);
    notifyUnsavedWork();
    this.listeners.forEach(listener => listener());
  }

  change = (patch: Partial<ItemDetailsData>, immediate = false) => {
    if (!Object.keys(patch).length) return;
    this.update({ patch: { ...this.state.patch, ...patch, ...(patch.measurements
      ? { measurements: mergeMeasurements(this.state.patch?.measurements, patch.measurements) } : {}) }, saving: true, error: null });
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (immediate) void this.flush();
    else this.timer = setTimeout(() => { this.timer = null; void this.flush(); }, 500);
  };

  retry = () => { if (!this.flushing && this.state.patch) void this.flush(); };

  /** Saves typing still waiting out the debounce, e.g. when the page is about to reload. */
  commit = () => { if (this.timer) void this.flush(); };

  private async flush() {
    if (this.flushing || !this.state.patch) return;
    this.flushing = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.update({ ...this.state, saving: true, error: null });
    try {
      while (this.state.patch) {
        const submitted = this.state.patch;
        // Inputs remain editable while their initial data loads. Read the base
        // before saving and overlay only fields the seller actually changed.
        const [conversation, dimensions] = await Promise.all([
          api.conversations.get(this.id),
          this.client.ensureQueryData({ queryKey: ["settings-package-dimensions"], queryFn: api.settings.packageDimensions }),
        ]);
        const changes = detailNoteChanges(conversation.notes, submitted, `${dimensions.length}x${dimensions.width}x${dimensions.height}`);
        const result = await api.conversations.update(this.id, { note_changes: changes });
        await this.client.cancelQueries({ queryKey: ["conversation", this.id], exact: true });
        this.client.setQueryData(["conversation", this.id], result);
        const pending = Object.fromEntries(Object.entries(this.state.patch).filter(([key, value]) =>
          !(key in submitted) || JSON.stringify(value) !== JSON.stringify(submitted[key as keyof ItemDetailsData])));
        this.update({ patch: Object.keys(pending).length ? pending : null, saving: Boolean(Object.keys(pending).length), error: null });
        if (submitted.vendooLabels !== undefined) {
          const before = new Set(splitLabels(parseNotes(conversation.notes).vendooLabels).map(label => label.toLowerCase()));
          const added = splitLabels(submitted.vendooLabels).filter(label => !before.has(label.toLowerCase()));
          void api.settings.setUi({ remember_labels: submitted.vendooLabels, ...(added.length ? { restore_labels: added } : {}) })
            .then(() => this.client.invalidateQueries({ queryKey: ["settings-ui"] })).catch(() => {});
        }
        void this.client.invalidateQueries({ queryKey: ["conversation", this.id] });
      }
    } catch (error) {
      this.update({ ...this.state, saving: false, error: error instanceof Error ? error.message : "Could not save seller details" });
    } finally {
      this.flushing = false;
      this.update(this.state);
    }
  }
}

const unsaved = new Set<ItemDetailsEdits>();
registerUnsavedWork({
  pending: () => unsaved.size > 0,
  commit: () => unsaved.forEach(edits => edits.commit()),
});

const queues = new WeakMap<QueryClient, Map<string, ItemDetailsEdits>>();
export function itemDetailsEdits(client: QueryClient, id: string) {
  let listings = queues.get(client);
  if (!listings) { listings = new Map(); queues.set(client, listings); }
  let edits = listings.get(id);
  if (!edits) { edits = new ItemDetailsEdits(client, id); listings.set(id, edits); }
  return edits;
}
