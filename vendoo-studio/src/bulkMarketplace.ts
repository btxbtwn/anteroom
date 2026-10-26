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

/**
 * One item at a time, each once. A failed item is never retried here, and a
 * lost reply is reported as unconfirmed so the seller checks before acting again.
 */
export async function runMarketplaceTasks(
  tasks: readonly MarketplaceTask[],
  perform: (body: BulkMarketplacePerform) => Promise<BulkMarketplaceResult>,
  stopped: () => boolean,
  progress: (tasks: MarketplaceTask[], activeId: string | null) => void,
): Promise<MarketplaceTask[]> {
  let result = tasks.map((task) => ({ ...task }));
  for (const task of tasks) {
    if (stopped()) break;
    if (task.state !== "pending") continue;
    progress(result, task.id);
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
    result = result.map((current) => (current.id === task.id ? { ...current, state, error } : current));
    progress(result, null);
  }
  return result;
}
