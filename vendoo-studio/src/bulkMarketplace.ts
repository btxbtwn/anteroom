import type { BulkMarketplacePerform, BulkMarketplaceResult, BulkMarketplaceRow } from "./api/bulkMarketplace";

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
        revision_id: row.revision_id, review_token: row.review_token, confirm: true },
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
