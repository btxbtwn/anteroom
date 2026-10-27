import type { Promotion, PromotionResult } from "./api/promotions";

export interface PromotionTask {
  id: string;
  title: string;
  shippingDiscountId?: string;
  state: "pending" | "done" | "failed" | "unconfirmed" | "stopped";
  note?: string;
}

/** Marketplaces whose offers carry a shipping discount the seller chooses. */
export const SHIPPING_DISCOUNT_MARKETPLACES: readonly string[] = ["poshmark"];

export function needsShippingChoice(row: Pick<Promotion, "action" | "marketplace">): boolean {
  return row.action === "offer" && SHIPPING_DISCOUNT_MARKETPLACES.includes(row.marketplace);
}

/** The reviewed rows the seller left checked. A Poshmark offer needs its shipping choice. */
export function promotionTasks(rows: readonly Promotion[], unchecked: ReadonlySet<string>, shipping: Readonly<Record<string, string>>): PromotionTask[] {
  return rows.flatMap((row) => {
    if (row.state !== "preview" || unchecked.has(row.id)) return [];
    if (needsShippingChoice(row) && !shipping[row.id]) return [];
    return [{ id: row.id, title: row.title || "Untitled item", state: "pending" as const,
      ...(needsShippingChoice(row) ? { shippingDiscountId: shipping[row.id] } : {}) }];
  });
}

/**
 * One item at a time, each once. A refusal that applies to the whole account
 * (rate limit, browser check, signed out) stops the rest; nothing is retried.
 */
export async function runPromotionTasks(
  tasks: readonly PromotionTask[],
  perform: (id: string, shippingDiscountId?: string) => Promise<PromotionResult>,
  stopped: () => boolean,
  progress: (tasks: PromotionTask[], activeId: string | null) => void,
): Promise<PromotionTask[]> {
  let result = tasks.map((task) => ({ ...task }));
  let halt: string | null = null;
  for (const task of tasks) {
    if (task.state !== "pending") continue;
    if (halt || stopped()) {
      result = result.map((current) => (current.id === task.id ? { ...current, state: "stopped", note: halt || "Stopped before sending." } : current));
      continue;
    }
    progress(result, task.id);
    let state: PromotionTask["state"];
    let note: string | undefined;
    try {
      const reply = await perform(task.id, task.shippingDiscountId);
      state = reply.state === "done" ? "done" : reply.state === "unknown" ? "unconfirmed" : "failed";
      note = reply.note || undefined;
      if (reply.stop) halt = `Stopped: ${reply.note || "the marketplace asked Anteroom to stop."}`;
      if (reply.state === "unknown") halt = "Stopped after an unconfirmed result. Check it before continuing.";
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : "";
      state = message && !/fetch|network|load failed/i.test(message) ? "failed" : "unconfirmed";
      note = state === "failed" ? message : "Anteroom did not answer. Check Recent actions before doing this again.";
      if (state === "unconfirmed") halt = "Stopped after an unconfirmed result. Check it before continuing.";
    }
    result = result.map((current) => (current.id === task.id ? { ...current, state, note } : current));
    progress(result, null);
  }
  progress(result, null);
  return result;
}
