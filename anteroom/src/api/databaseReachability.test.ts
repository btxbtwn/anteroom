import { describe, expect, it, vi } from "vitest";
import { createReachability } from "./databaseReachability";

function harness(answers: boolean[]) {
  const timers: Array<() => void> = [];
  const probe = vi.fn(async () => answers.shift() ?? false);
  const store = createReachability(probe, run => { timers.push(run); return () => { timers.splice(timers.indexOf(run), 1); }; });
  const tick = async () => { const run = timers.shift(); run?.(); await Promise.resolve(); await Promise.resolve(); };
  return { store, probe, timers, tick };
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

describe("database reachability", () => {
  it("stays ok when one 503 is not confirmed by /api/ready", async () => {
    const { store, probe, timers } = harness([true]);
    store.note("https://myanteroom.com");
    expect(store.get()).toBe("checking");
    await settle();
    expect(probe).toHaveBeenCalledWith("https://myanteroom.com");
    expect(store.get()).toBe("ok");
    expect(timers).toHaveLength(0);
  });

  it("says unreachable while /api/ready fails, polls every 5 s and recovers only on a pass", async () => {
    const { store, probe, timers, tick } = harness([false, false, true]);
    const recovered = vi.fn();
    store.onRecovered(recovered);
    store.note("https://myanteroom.com");
    await settle();
    expect(store.get()).toBe("unreachable");
    store.note("https://myanteroom.com"); // More failing reads do not start more checks.
    expect(timers).toHaveLength(1);
    await tick();
    expect(store.get()).toBe("unreachable");
    expect(recovered).not.toHaveBeenCalled();
    await tick();
    expect(store.get()).toBe("ok");
    expect(recovered).toHaveBeenCalledOnce();
    expect(probe).toHaveBeenCalledTimes(3);
    expect(timers).toHaveLength(0);
  });

  it("stops polling when the session ends", async () => {
    const { store, timers } = harness([false]);
    store.note("https://myanteroom.com");
    await settle();
    store.reset();
    expect(store.get()).toBe("ok");
    expect(timers).toHaveLength(0);
  });
});
