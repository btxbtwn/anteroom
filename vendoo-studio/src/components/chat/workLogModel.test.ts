import { describe, expect, it } from "vitest";
import { applyAgentEvent, parseAgentEvent, settleAgents, type LiveAgents, type LiveTraceEntry } from "../liveTrace";
import { consumeResponseSse } from "./chatStream";
import {
  agentsFor,
  deriveAgentSpawnSummary,
  formatAgentElapsed,
  groupWorkLog,
  placeTurnFolds,
  stepAction,
  summarizeSteps,
  turnFoldLabel,
} from "./workLogModel";

const agentEvent = (fields: Record<string, unknown>) =>
  `event: agent\ndata: ${JSON.stringify({ detail: null, started_at: 1_000, ended_at: null, ...fields })}\n\n`;

function sseResponse(body: string): Response {
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

describe("agent events", () => {
  it("parses the backend's JSON and rejects anything malformed", () => {
    expect(parseAgentEvent('{"id":"a1","label":"eBay comps","status":"running","detail":null,"started_at":5,"ended_at":null}'))
      .toEqual({ id: "a1", label: "eBay comps", status: "running", detail: null, startedAt: 5, endedAt: null });
    expect(parseAgentEvent("not json")).toBeNull();
    expect(parseAgentEvent('{"label":"no id"}')).toBeNull();
    expect(parseAgentEvent('{"id":"a","status":"exploded","started_at":1}', 9)?.status).toBe("running");
    // A finished agent without an end time ends now.
    expect(parseAgentEvent('{"id":"a","status":"done","started_at":1}', 9)?.endedAt).toBe(9);
  });

  it("updates an agent in place and groups new agents where they started", () => {
    let trace: LiveTraceEntry[] = [{ kind: "phase", text: "Looking up comps…" }];
    let agents: LiveAgents = {};
    const apply = (raw: Record<string, unknown>) => {
      const agent = parseAgentEvent(JSON.stringify({ started_at: 1, ended_at: null, detail: null, ...raw }))!;
      ({ trace, agents } = applyAgentEvent(trace, agents, agent));
    };
    apply({ id: "a", label: "eBay", status: "running" });
    apply({ id: "b", label: "Poshmark", status: "running" });
    apply({ id: "a", label: "eBay", status: "running", detail: "Searching sold listings" });
    apply({ id: "a", label: "eBay", status: "done", ended_at: 4, detail: null });
    expect(trace).toEqual([
      { kind: "phase", text: "Looking up comps…" },
      { kind: "agents", ids: ["a", "b"] },
    ]);
    // The last detail survives an update that carries none.
    expect(agents.a).toMatchObject({ status: "done", endedAt: 4, detail: "Searching sold listings" });
    expect(agents.b.status).toBe("running");
  });

  it("parses agent events off the wire, before any text, without losing status changes to batching", async () => {
    const body = [
      agentEvent({ id: "a", label: "eBay comps", status: "running" }),
      agentEvent({ id: "b", label: "Mercari comps", status: "running" }),
      agentEvent({ id: "c", label: "Depop comps", status: "running" }),
      agentEvent({ id: "a", label: "eBay comps", status: "done", ended_at: 3_000, detail: "12 sold" }),
      agentEvent({ id: "b", label: "Mercari comps", status: "failed", ended_at: 3_500, detail: "Timed out" }),
      "event: step\ndata: Searched Brave: levis 501\n\n",
      "data: Here is the listing.\n\n",
      "data: [DONE]\n\n",
    ].join("");
    const events: string[] = [];
    const { parts, sawDone } = await consumeResponseSse(sseResponse(body), (event) => events.push(event));
    expect(sawDone).toBe(true);
    expect(parts.content).toBe("Here is the listing.");
    expect(parts.trace).toEqual([
      { kind: "agents", ids: ["a", "b", "c"] },
      { kind: "step", text: "Searched Brave: levis 501" },
    ]);
    expect(Object.values(parts.agents).map((agent) => agent.status)).toEqual(["done", "failed", "running"]);
    expect(events[0]).toBe("agent");
    expect(deriveAgentSpawnSummary(agentsFor(["a", "b", "c"], parts.agents))).toEqual({
      live: true,
      lead: "Kicked off 3 subagents",
      status: "1 working",
      tone: "working",
    });
  });

  it("rebuilds the same state from a replay when the client re-attaches", async () => {
    const body = [
      agentEvent({ id: "a", label: "eBay comps", status: "running" }),
      agentEvent({ id: "a", label: "eBay comps", status: "done", ended_at: 2_000 }),
    ].join("");
    const first = await consumeResponseSse(sseResponse(body), () => undefined);
    const replay = await consumeResponseSse(sseResponse(body), () => undefined);
    expect(replay.parts.trace).toEqual(first.parts.trace);
    expect(replay.parts.agents).toEqual(first.parts.agents);
  });
});

describe("deriveAgentSpawnSummary", () => {
  it("reads like T3 Code once the agents settle", () => {
    expect(deriveAgentSpawnSummary([{ status: "done" }, { status: "done" }, { status: "done" }]))
      .toMatchObject({ live: false, lead: "Ran 3 subagents", status: "✓ completed", tone: "completed" });
    expect(deriveAgentSpawnSummary([{ status: "done" }, { status: "failed" }]))
      .toMatchObject({ lead: "Ran 2 subagents", status: "1 failed", tone: "failed" });
    expect(deriveAgentSpawnSummary([{ status: "cancelled" }]))
      .toMatchObject({ lead: "Ran 1 subagent", status: "1 stopped", tone: "inactive" });
  });

  it("shows agents a finished turn left running as stopped", () => {
    const agents: LiveAgents = {
      a: { id: "a", label: "A", status: "running", detail: null, startedAt: 1, endedAt: null },
      b: { id: "b", label: "B", status: "done", detail: null, startedAt: 1, endedAt: 2 },
    };
    const settled = settleAgents(agents, 50);
    expect(settled.a).toMatchObject({ status: "cancelled", endedAt: 50 });
    expect(settled.b).toBe(agents.b);
  });

  it("formats elapsed time like AgentElapsed", () => {
    expect(formatAgentElapsed(42.9)).toBe("42s");
    expect(formatAgentElapsed(187)).toBe("3m 07s");
    expect(formatAgentElapsed(3_900)).toBe("1h 05m");
  });
});

describe("summarizeSteps", () => {
  it("classifies the steps the server writes", () => {
    expect(stepAction("Searched Brave: levis 501")).toBe("search");
    expect(stepAction("eBay is searching the web for sold comps")).toBe("search");
    expect(stepAction("eBay found 12 sold, 3 live")).toBe("found");
    expect(stepAction("Mercari web search timed out")).toBe("failed");
    expect(stepAction("Read the eBay category schema")).toBe("schema");
    expect(stepAction("Cursor: thinking")).toBe("other");
  });

  it("joins the two most telling actions and counts the rest", () => {
    expect(summarizeSteps(["Searched Brave: a", "Searched Brave: b", "Searched Brave: c"]))
      .toEqual({ summary: "Searched 3 times", hasFailure: false });
    expect(summarizeSteps(["Searched Brave: a", "Read the eBay schema", "Read the Etsy schema"]).summary)
      .toBe("Searched 1 time and read 2 schemas");
    expect(summarizeSteps(["Searched: a", "eBay found 3 sold", "Mercari web search failed", "Cursor: x"]))
      .toEqual({ summary: "Searched 1 time, 1 step failed, and performed 2 other actions", hasFailure: true });
  });

  it("groups only consecutive steps", () => {
    const items = groupWorkLog([
      { kind: "phase", text: "Looking up comps…" },
      { kind: "step", text: "Searched: a" },
      { kind: "step", text: "Searched: b" },
      { kind: "thought", text: "hmm" },
      { kind: "step", text: "Searched: c" },
    ]);
    expect(items.map((item) => item.kind)).toEqual(["phase", "steps", "thought", "step"]);
    expect(items[1]).toEqual({ kind: "steps", index: 1, texts: ["Searched: a", "Searched: b"] });
  });
});

describe("turn fold", () => {
  it("labels finished and stopped turns", () => {
    expect(turnFoldLabel({ startedAtMs: 0, endedAtMs: 63_000, cancelled: false })).toBe("Worked for 1m 3s");
    expect(turnFoldLabel({ startedAtMs: 0, endedAtMs: 4_200, cancelled: true })).toBe("You stopped after 4.2s");
  });

  it("sits above the answer each turn produced", () => {
    const messages = [
      { id: "u1", role: "user" },
      { id: "e1", role: "system" },
      { id: "a1", role: "assistant" },
      { id: "u2", role: "user" },
      { id: "a2", role: "assistant" },
    ];
    expect(placeTurnFolds(messages, [{ baseIds: ["u1"] }, { baseIds: ["u1", "e1", "a1"] }])).toEqual([1, 4]);
  });

  it("puts a turn that is still landing at the end, and a stopped empty turn after its question", () => {
    const messages = [{ id: "u1", role: "user" }, { id: "u2", role: "user" }, { id: "a2", role: "assistant" }];
    // Stopped before answering: only the question was saved.
    expect(placeTurnFolds(messages, [{ baseIds: [] }, { baseIds: ["u1"] }])).toEqual([1, 2]);
    // The answer has not been refetched yet.
    expect(placeTurnFolds([{ id: "u1", role: "user" }], [{ baseIds: [] }])).toEqual([1]);
    expect(placeTurnFolds([{ id: "u1", role: "user" }], [{ baseIds: ["u1"] }])).toEqual([1]);
  });
});
