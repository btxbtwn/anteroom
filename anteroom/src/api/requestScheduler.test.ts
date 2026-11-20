import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAX_BACKGROUND_CONCURRENT,
  promoteBackgroundSignal,
  registerBackgroundSignal,
  runInBackgroundContext,
  scheduleRequest,
} from "./requestScheduler";

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(() => vi.restoreAllMocks());

describe("requestScheduler", () => {
  it("pauses background requests while an interactive request is in flight", async () => {
    const order: string[] = [];
    const interactive = deferred<string>();
    const bgController = new AbortController();
    registerBackgroundSignal(bgController.signal);

    const pInteractive = scheduleRequest("/api/conversations/1/screen", {}, () => {
      order.push("interactive:start");
      return interactive.promise;
    });

    const pBg = scheduleRequest("/api/sourcing", { signal: bgController.signal }, async () => {
      order.push("background:start");
      return "bg";
    });

    await Promise.resolve();
    expect(order).toEqual(["interactive:start"]);

    interactive.resolve("ok");
    expect(await pInteractive).toBe("ok");
    expect(await pBg).toBe("bg");
    expect(order).toEqual(["interactive:start", "background:start"]);
  });

  it("caps concurrent background requests at MAX_BACKGROUND_CONCURRENT", async () => {
    const gates = Array.from({ length: 4 }, () => deferred<number>());
    let running = 0;
    let peak = 0;
    const started: number[] = [];

    const tasks = gates.map((gate, index) => {
      const controller = new AbortController();
      registerBackgroundSignal(controller.signal);
      return scheduleRequest(`/api/bg/${index}`, { signal: controller.signal }, async () => {
        started.push(index);
        running += 1;
        peak = Math.max(peak, running);
        try {
          return await gate.promise;
        } finally {
          running -= 1;
        }
      });
    });

    await vi.waitFor(() => expect(started).toHaveLength(MAX_BACKGROUND_CONCURRENT));
    expect(peak).toBe(MAX_BACKGROUND_CONCURRENT);
    expect(started).toEqual([0, 1]);

    gates[0].resolve(10);
    await vi.waitFor(() => expect(started).toEqual([0, 1, 2]));
    gates[1].resolve(20);
    await vi.waitFor(() => expect(started).toEqual([0, 1, 2, 3]));
    gates[2].resolve(30);
    gates[3].resolve(40);

    expect(await Promise.all(tasks)).toEqual([10, 20, 30, 40]);
    expect(peak).toBe(MAX_BACKGROUND_CONCURRENT);
  });

  it("aborts queued background requests without ever executing them", async () => {
    const interactive = deferred<void>();
    const pInteractive = scheduleRequest("/api/conversations", {}, () => interactive.promise);

    const controller = new AbortController();
    registerBackgroundSignal(controller.signal);
    const run = vi.fn(async () => "never");
    const queued = scheduleRequest("/api/conversations/2/screen", { signal: controller.signal }, run);

    controller.abort(new DOMException("Hovered away", "AbortError"));
    await expect(queued).rejects.toMatchObject({ name: "AbortError" });

    interactive.resolve();
    await pInteractive;
    expect(run).not.toHaveBeenCalled();
  });

  it("promotes a queued background request immediately when an interactive caller joins it", async () => {
    const interactive = deferred<void>();
    const pInteractive = scheduleRequest("/api/conversations", {}, () => interactive.promise);

    const controller = new AbortController();
    registerBackgroundSignal(controller.signal);
    let ran = false;
    const queued = scheduleRequest("/api/conversations/2/screen", { signal: controller.signal }, async () => {
      ran = true;
      return "promoted";
    });

    await Promise.resolve();
    expect(ran).toBe(false);

    promoteBackgroundSignal(controller.signal);
    expect(await queued).toBe("promoted");
    expect(ran).toBe(true);

    interactive.resolve();
    await pInteractive;
  });

  it("excludes SSE streams and long-poll endpoints from blocking background work", async () => {
    const sse = deferred<void>();
    const relay = deferred<void>();
    const pSse = scheduleRequest(
      "/api/conversations/1/messages",
      { method: "POST", headers: { Accept: "text/event-stream" } },
      () => sse.promise,
    );
    const pRelay = scheduleRequest("/api/browser-relay/next?capabilities=a", {}, () => relay.promise);

    const bg = runInBackgroundContext(() =>
      scheduleRequest("/api/boxes", {}, async () => "boxes-ok"),
    );

    expect(await bg).toBe("boxes-ok");
    sse.resolve();
    relay.resolve();
    await Promise.all([pSse, pRelay]);
  });
});
