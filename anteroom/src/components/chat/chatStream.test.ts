import { describe, expect, it, vi } from "vitest";
import { STREAM_IDLE_MS, StreamIdleError } from "../../api/sse";
import { consumeResponseSse } from "./chatStream";

describe("consumeResponseSse", () => {
  it("finishes at DONE without reading a failing connection teardown", async () => {
    let reads = 0;
    const reader = {
      read: async () => {
        if (reads++ === 0) return { done: false, value: new TextEncoder().encode("data: Saved\n\ndata: [DONE]\n\n") };
        throw new TypeError("Load failed");
      },
      cancel: async () => {},
    };
    const response = { body: { getReader: () => reader } } as unknown as Response;
    const result = await consumeResponseSse(response, () => {});
    expect(result.sawDone).toBe(true);
    expect(result.parts.content).toBe("Saved");
    expect(reads).toBe(1);
  });
  it("keeps agent events out of the reply text", async () => {
    const agent = JSON.stringify({
      id: "comps-sold-1",
      label: "Sold comps",
      status: "done",
      detail: "Found 8 sold comps",
      started_at: 1,
      ended_at: 2,
    });
    const body = [
      "event: agent",
      `data: ${agent}`,
      "",
      "data: Error: Photo analysis failed.",
      "",
      "data: [DONE]",
      "",
    ].join("\n");
    const { parts, sawDone } = await consumeResponseSse(new Response(body), () => {});
    expect(sawDone).toBe(true);
    expect(parts.content).toBe("Error: Photo analysis failed.");
    expect(parts.content).not.toContain("Sold comps");
  });
  it("treats a connection that goes silent as dropped, so generation reattaches", async () => {
    vi.useFakeTimers();
    try {
      let cancelled = false;
      const body = new ReadableStream<Uint8Array>({
        start(controller) { controller.enqueue(new TextEncoder().encode("event: status\ndata: Reading photos\n\n")); },
        cancel() { cancelled = true; },
      });
      const failed = expect(consumeResponseSse(new Response(body), () => {})).rejects.toBeInstanceOf(StreamIdleError);
      await vi.advanceTimersByTimeAsync(STREAM_IDLE_MS);
      await failed;
      expect(cancelled).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
