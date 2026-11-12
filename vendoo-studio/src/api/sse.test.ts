import { afterEach, describe, expect, it, vi } from "vitest";
import { readSse, STREAM_IDLE_MS, StreamIdleError } from "./sse";

afterEach(() => { vi.useRealTimers(); });

function streamed(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body);
}

describe("readSse", () => {
  it("joins events split across chunks and multi-line data", async () => {
    const events: [string, string][] = [];
    await readSse(
      streamed([
        'event: source\ndata: {"source":"Cur',
        'sor"}\n\n: keepalive\n\nevent: step\ndata: one\ndata: two\n\n',
        "data: [DONE]\n\n",
      ]),
      (event, data) => events.push([event, data]),
    );
    expect(events).toEqual([
      ["source", '{"source":"Cursor"}'],
      ["step", "one\ntwo"],
    ]);
  });

  it("gives up on a connection that goes silent, as a network failure", async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(encoder.encode("event: step\ndata: one\n\n")); },
      cancel() { cancelled = true; },
    });
    const events: string[] = [];
    const failed = expect(readSse(new Response(body), (_event, data) => events.push(data))).rejects.toBeInstanceOf(StreamIdleError);
    await vi.advanceTimersByTimeAsync(STREAM_IDLE_MS);
    await failed;
    expect(events).toEqual(["one"]);
    expect(cancelled).toBe(true);
  });

  it("keeps a stream open while keepalives arrive", async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    let push!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(controller) { push = controller; } });
    const reading = readSse(new Response(body), () => {});
    for (let i = 0; i < 6; i += 1) {
      await vi.advanceTimersByTimeAsync(STREAM_IDLE_MS / 2);
      push.enqueue(encoder.encode(": keepalive\n\n"));
    }
    push.close();
    await expect(reading).resolves.toBeUndefined();
  });
});
