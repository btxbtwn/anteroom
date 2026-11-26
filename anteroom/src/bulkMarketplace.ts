import type { BulkMarketplacePerform, BulkMarketplaceResult, BulkMarketplaceRow } from "./api/bulkMarketplace";
import type { MarketplaceTextResult, RuleMarketplace, TextMode } from "./api/listingRules";

export interface MarketplaceTask {
  id: string;
  title: string;
  request: BulkMarketplacePerform;
  state: "pending" | "done" | "failed" | "unconfirmed";
  error?: string;
}

/** The reviewed items the seller left checked, each with the exact reviewed request. */
export function reviewedTasks(rows: readonly BulkMarketplaceRow[], unchecked: ReadonlySet<string>): MarketplaceTask[] {
  return rows.flatMap((row) => {
    if (row.skip_reason || !row.review_token || !row.marketplaces.length || unchecked.has(row.conversation_id)) return [];
    return [{
      id: row.conversation_id,
      title: row.title || "Untitled item",
      state: "pending" as const,
      request: { conversation_id: row.conversation_id, action: row.action, marketplaces: row.marketplaces,
        revision_id: row.revision_id, review_token: row.review_token, confirm: true,
        ...(row.ebay_ad_rate ? { confirmed_ebay_ad_rate: row.ebay_ad_rate } : {}) },
    }];
  });
}

function failureText(result: BulkMarketplaceResult): string {
  const errors = Object.entries(result.errors || {}).map(([name, error]) => `${name}: ${error}`);
  return errors.length ? errors.join("; ") : result.error || "The marketplace did not confirm this action.";
}

/** List and Update run this many items at once; the server takes turns on each marketplace. */
export const ITEMS_AT_ONCE = 3;
const SIDE_BY_SIDE = new Set<BulkMarketplacePerform["action"]>(["list", "update"]);

/**
 * Each item once, a few at a time for List and Update, otherwise one at a
 * time. A failed item is never retried here, and a lost reply is reported as
 * unconfirmed so the seller checks before acting again.
 */
export async function runMarketplaceTasks(
  tasks: readonly MarketplaceTask[],
  perform: (body: BulkMarketplacePerform) => Promise<BulkMarketplaceResult>,
  stopped: () => boolean,
  progress: (tasks: MarketplaceTask[], activeIds: ReadonlySet<string>) => void,
): Promise<MarketplaceTask[]> {
  let result = tasks.map((task) => ({ ...task }));
  const queue = tasks.filter((task) => task.state === "pending");
  const active = new Set<string>();
  const slots = queue.every((task) => SIDE_BY_SIDE.has(task.request.action)) ? ITEMS_AT_ONCE : 1;

  async function worker() {
    for (let task = queue.shift(); task && !stopped(); task = queue.shift()) {
      active.add(task.id);
      progress(result, new Set(active));
      let state: MarketplaceTask["state"];
      let error: string | undefined;
      try {
        const reply = await perform(task.request);
        state = reply.ok ? "done" : "failed";
        error = reply.ok ? undefined : failureText(reply);
      } catch (failure) {
        const message = failure instanceof Error ? failure.message : "";
        // A refusal from Anteroom (changed item, busy slot) means nothing was sent.
        state = message && !/fetch|network|load failed/i.test(message) ? "failed" : "unconfirmed";
        error = state === "failed" ? message : "Anteroom did not answer. Review again to see what each marketplace has now.";
      }
      const id = task.id;
      active.delete(id);
      result = result.map((current) => (current.id === id ? { ...current, state, error } : current));
      progress(result, new Set(active));
    }
  }

  await Promise.all(Array.from({ length: Math.min(slots, queue.length) }, worker));
  return result;
}

export interface TextPrepResult {
  id: string;
  ok: boolean;
  /** Fields saved with new text on this item. */
  changed: number;
  /** Fields the AI left out, which keep their text. */
  skipped: number;
  error?: string;
}

/**
 * Prepare each item's marketplace text before review, a few items at a time.
 * Each item's text is saved as a local edit; nothing is sent. A failed item
 * keeps its text as it was and is reported, and the others go on.
 */
export async function prepareMarketplaceText(
  ids: readonly string[],
  marketplaces: RuleMarketplace[],
  mode: TextMode,
  prepare: (id: string, body: { marketplaces: RuleMarketplace[]; mode: TextMode }) => Promise<MarketplaceTextResult>,
  progress: (done: number) => void = () => {},
): Promise<TextPrepResult[]> {
  if (mode === "keep" || !ids.length) return ids.map((id) => ({ id, ok: true, changed: 0, skipped: 0 }));
  const queue = [...ids];
  const results = new Map<string, TextPrepResult>();
  async function worker() {
    for (let id = queue.shift(); id; id = queue.shift()) {
      try {
        const reply = await prepare(id, { marketplaces, mode });
        results.set(id, { id, ok: true, changed: reply.changes.length, skipped: reply.skipped.length });
      } catch (failure) {
        results.set(id, { id, ok: false, changed: 0, skipped: 0, error: failure instanceof Error ? failure.message : "Could not prepare the text." });
      }
      progress(results.size);
    }
  }
  await Promise.all(Array.from({ length: Math.min(ITEMS_AT_ONCE, queue.length) }, worker));
  return ids.map((id) => results.get(id)!);
}
