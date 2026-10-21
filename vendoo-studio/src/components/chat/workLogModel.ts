/**
 * Presentation logic for a turn's work log: grouped step summaries, subagent
 * group summaries, and where a finished turn's "Worked for" fold sits.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.): `summarizeToolGroup` in
 * packages/client-runtime/src/work-log/presentation.ts, `deriveAgentSpawnSummary`
 * in components/chat/agentSpawnSummary.ts, the elapsed formatting in
 * components/chat/AgentElapsed.tsx, and the turn fold in
 * components/chat/MessagesTimeline.logic.ts.
 */
import { formatDuration } from "../workingTimerFormat";
import type { LiveAgent, LiveAgents, LiveTraceEntry } from "../liveTrace";

// ── Grouped step summaries ──────────────────────────────────────────────

type StepAction = "search" | "found" | "schema" | "read" | "failed" | "other";

/** What kind of action one `step` line reports. Steps are short sentences the server writes. */
export function stepAction(text: string): StepAction {
  if (/\b(failed|timed out|error)\b/i.test(text)) return "failed";
  if (/\bschemas?\b/i.test(text)) return "schema";
  if (/\bsearch(?:ed|es|ing)?\b/i.test(text)) return "search";
  if (/\bfound\b/i.test(text)) return "found";
  if (/\b(read|opened|looked up|checked)\b/i.test(text)) return "read";
  return "other";
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function stepActionLabel(action: StepAction, count: number): string {
  switch (action) {
    case "search":
      return `Searched ${plural(count, "time", "times")}`;
    case "found":
      return `Collected ${plural(count, "result", "results")}`;
    case "schema":
      return `Read ${plural(count, "schema", "schemas")}`;
    case "read":
      return `Read ${plural(count, "page", "pages")}`;
    case "failed":
      return `${plural(count, "step", "steps")} failed`;
    case "other":
      return `Performed ${plural(count, "action", "actions")}`;
  }
}

/** Lower is shown first when only two labels fit, like T3's summaryActionPriority. */
function stepActionPriority(action: StepAction): number {
  if (action === "failed") return 0;
  if (action === "other") return 2;
  return 1;
}

/** "Searched 3 times and read 2 schemas": one line for a run of consecutive steps. */
export function summarizeSteps(texts: readonly string[]): { summary: string; hasFailure: boolean } {
  const groups = new Map<StepAction, number>();
  for (const text of texts) {
    const action = stepAction(text);
    groups.set(action, (groups.get(action) ?? 0) + 1);
  }
  const summaries = [...groups].map(([action, count], index) => ({
    action,
    count,
    index,
    priority: stepActionPriority(action),
  }));
  const selected = [...summaries]
    .sort((a, b) => a.priority - b.priority || a.index - b.index)
    .slice(0, 2)
    .sort((a, b) => a.index - b.index);
  const labels = selected.map(({ action, count }) => stepActionLabel(action, count));
  const remaining = texts.length - selected.reduce((sum, group) => sum + group.count, 0);
  if (remaining > 0) labels.push(`Performed ${remaining} other ${remaining === 1 ? "action" : "actions"}`);
  const sentence = labels.map((label, index) =>
    index === 0 ? label : label.charAt(0).toLowerCase() + label.slice(1),
  );
  const summary = sentence.length < 3
    ? sentence.join(" and ")
    : `${sentence.slice(0, -1).join(", ")}, and ${sentence[sentence.length - 1]}`;
  return { summary, hasFailure: groups.has("failed") };
}

/** One row of the rendered work log. Runs of two or more steps become one group. */
export type WorkLogItem =
  | { kind: "phase"; index: number; text: string }
  | { kind: "thought"; index: number; text: string }
  | { kind: "posted"; index: number; id: string }
  | { kind: "step"; index: number; text: string }
  | { kind: "steps"; index: number; texts: string[] }
  | { kind: "agents"; index: number; ids: string[] };

export function groupWorkLog(trace: readonly LiveTraceEntry[]): WorkLogItem[] {
  const items: WorkLogItem[] = [];
  for (const [index, entry] of trace.entries()) {
    if (entry.kind === "step") {
      const last = items[items.length - 1];
      if (last?.kind === "steps") {
        last.texts.push(entry.text);
      } else if (last?.kind === "step") {
        items[items.length - 1] = { kind: "steps", index: last.index, texts: [last.text, entry.text] };
      } else {
        items.push({ kind: "step", index, text: entry.text });
      }
      continue;
    }
    if (entry.kind === "phase") items.push({ kind: "phase", index, text: entry.text });
    else if (entry.kind === "thought") items.push({ kind: "thought", index, text: entry.text });
    else if (entry.kind === "posted") items.push({ kind: "posted", index, id: entry.id });
    else items.push({ kind: "agents", index, ids: [...entry.ids] });
  }
  return items;
}

// ── Subagents ───────────────────────────────────────────────────────────

export type AgentSummaryTone = "working" | "completed" | "failed" | "inactive";

/** "Kicked off 3 subagents · 2 working", then "Ran 3 subagents · ✓ completed". */
export function deriveAgentSpawnSummary(agents: readonly Pick<LiveAgent, "status">[]): {
  live: boolean;
  lead: string;
  status: string;
  tone: AgentSummaryTone;
} {
  const working = agents.filter((agent) => agent.status === "running").length;
  const failed = agents.filter((agent) => agent.status === "failed").length;
  const stopped = agents.filter((agent) => agent.status === "cancelled").length;
  const live = working > 0;
  const lead = `${live ? "Kicked off" : "Ran"} ${agents.length ? plural(agents.length, "subagent", "subagents") : "subagents"}`;
  const status = live
    ? `${working} working`
    : failed > 0
      ? `${failed} failed`
      : stopped > 0
        ? `${stopped} stopped`
        : "✓ completed";
  const tone: AgentSummaryTone = live ? "working" : failed > 0 ? "failed" : stopped > 0 ? "inactive" : "completed";
  return { live, lead, status, tone };
}

/** "2 working · 1 done · 1 failed", like T3's summarizeSubagentStatuses. */
export function summarizeAgentStatuses(agents: readonly Pick<LiveAgent, "status">[]): string {
  const counts = { working: 0, done: 0, failed: 0, stopped: 0 };
  for (const { status } of agents) {
    if (status === "running") counts.working += 1;
    else if (status === "done") counts.done += 1;
    else if (status === "failed") counts.failed += 1;
    else counts.stopped += 1;
  }
  return (Object.keys(counts) as (keyof typeof counts)[])
    .filter((key) => counts[key] > 0)
    .map((key) => `${counts[key]} ${key}`)
    .join(" · ");
}

/** The group's span: first start to last end, open while any agent runs. */
export function agentGroupTiming(agents: readonly Pick<LiveAgent, "status" | "startedAt" | "endedAt">[]): {
  startedAt: number;
  endedAt: number | null;
} | null {
  if (!agents.length) return null;
  const startedAt = Math.min(...agents.map((agent) => agent.startedAt));
  const live = agents.some((agent) => agent.status === "running");
  const ends = agents.map((agent) => agent.endedAt ?? startedAt);
  return { startedAt, endedAt: live ? null : Math.max(...ends) };
}

export function agentsFor(ids: readonly string[], agents: LiveAgents): LiveAgent[] {
  return ids.flatMap((id) => (agents[id] ? [agents[id]] : []));
}

/** T3's AgentElapsed format: 42s, 3m 07s, 1h 05m. */
export function formatAgentElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  if (minutes === 0) return `${seconds}s`;
  const hours = Math.floor(minutes / 60);
  if (hours === 0) return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
  return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
}

export function agentElapsedMs(agent: Pick<LiveAgent, "startedAt" | "endedAt">, nowMs: number): number {
  return Math.max(0, (agent.endedAt ?? nowMs) - agent.startedAt);
}

// ── Turn fold ───────────────────────────────────────────────────────────

/** A finished turn's work, kept for the session so its "Worked for" row can expand. */
export interface FoldedTurn {
  id: string;
  trace: LiveTraceEntry[];
  agents: LiveAgents;
  startedAtMs: number;
  endedAtMs: number;
  cancelled: boolean;
  /** Message ids that existed when the turn started; the turn's own messages are the rest. */
  baseIds: string[];
}

export function turnFoldLabel(turn: Pick<FoldedTurn, "startedAtMs" | "endedAtMs" | "cancelled">): string {
  const elapsed = turn.endedAtMs - turn.startedAtMs;
  const duration = Number.isFinite(elapsed) && elapsed >= 0 ? formatDuration(elapsed) : null;
  if (turn.cancelled) return duration ? `You stopped after ${duration}` : "You stopped this response";
  return duration ? `Worked for ${duration}` : "Worked";
}

/** Whether a fold has anything to show once expanded. */
export function turnHasWork(trace: readonly LiveTraceEntry[]): boolean {
  return trace.some((entry) => entry.kind !== "posted");
}

/**
 * Where each fold row goes in the message list: before the first message the
 * turn produced that is not the seller's own (the answer, or a card it posted),
 * so the answer stays visible right below "Worked for". A turn's messages are
 * the ones missing from its own `baseIds` but present in the next turn's.
 * A turn that produced nothing yet sits after its last message, or at the end.
 *
 * Returns, per fold (same order), the index of the message it sits before;
 * `messages.length` means after the last one.
 */
export function placeTurnFolds(
  messages: readonly { id: string; role: string }[],
  folds: readonly Pick<FoldedTurn, "baseIds">[],
): number[] {
  return folds.map((fold, k) => {
    const base = new Set(fold.baseIds);
    const next = folds[k + 1] ? new Set(folds[k + 1].baseIds) : null;
    let lastOwn = -1;
    for (const [index, message] of messages.entries()) {
      const id = String(message.id);
      if (base.has(id)) continue;
      if (next && !next.has(id)) continue;
      if (message.role !== "user") return index;
      lastOwn = index;
    }
    if (lastOwn >= 0) return lastOwn + 1;
    let lastBase = -1;
    for (const [index, message] of messages.entries()) {
      if (base.has(String(message.id))) lastBase = index;
    }
    return next ? lastBase + 1 : messages.length;
  });
}
