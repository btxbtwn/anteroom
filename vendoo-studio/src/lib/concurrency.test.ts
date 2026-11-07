import { describe, expect, it } from "vitest";
import { mapWithConcurrency } from "./concurrency";

const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe("mapWithConcurrency", () => {
  it("never runs more than the limit at once and keeps results in input order", async () => {
    let running = 0;
    let peak = 0;
    const items = [5, 1, 4, 2, 3, 0, 6, 2, 1, 3];
    const results = await mapWithConcurrency(items, 3, async (item, index) => {
      running += 1;
      peak = Math.max(peak, running);
      for (let i = 0; i < item; i += 1) await tick();
      running -= 1;
      return `${index}:${item}`;
    });
    expect(peak).toBe(3);
    expect(results).toEqual(items.map((item, index) => `${index}:${item}`));
  });

  it("rejects with the first failure and starts no further work", async () => {
    const started: number[] = [];
    const run = mapWithConcurrency([0, 1, 2, 3, 4], 2, async (item) => {
      started.push(item);
      await tick();
      if (item === 1) throw new Error("boom");
      return item;
    });
    await expect(run).rejects.toThrow("boom");
    await tick();
    expect(started).toEqual([0, 1, 2]);
  });

  it("handles no items and a limit larger than the list", async () => {
    expect(await mapWithConcurrency([], 4, async () => 1)).toEqual([]);
    expect(await mapWithConcurrency([1, 2], 10, async (n) => n * 2)).toEqual([2, 4]);
  });
});
