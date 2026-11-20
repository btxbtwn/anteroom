import { describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "../api/transport";
import { busyFromJobs, isBusyRefusal, MarketplaceWaitlist, type WaitingAction } from "./marketplaceWaitlist";

const busyRefusal = () => new ApiRequestError("Another Depop action is running. Wait for it to finish.", 409, false, null, "marketplace_busy");
const item = (convId: string, marketplace: WaitingAction["marketplace"] = "depop") =>
  ({ convId, title: `Item ${convId}`, marketplace, kind: "list" as const, revisionId: `rev-${convId}` });
const free = () => false;
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("marketplace waitlist", () => {
  it("queues confirmed delists across items and keeps every other action out until they finish", async () => {
    let finish!: () => void;
    const send = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const waitlist = new MarketplaceWaitlist(send);
    waitlist.add({ ...item("a"), kind: "delist" });
    waitlist.add({ ...item("b", "mercari"), kind: "delist" });
    waitlist.add(item("c", "ebay"));
    waitlist.setBusy((marketplace) => marketplace === "etsy");
    expect(send).not.toHaveBeenCalled();
    expect(waitlist.snapshot()).toHaveLength(3);
    waitlist.setBusy(free);
    waitlist.tick();
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]).toBeDefined();
    finish();
    await settle();
    waitlist.setBusy(free);
    expect(send).toHaveBeenCalledTimes(2);
    expect(waitlist.snapshot()[0]).toMatchObject({ convId: "b", starting: true });
    finish();
    await settle();
    waitlist.setBusy(free);
    expect(send).toHaveBeenCalledTimes(3);
    finish();
    await settle();
    expect(waitlist.snapshot()).toEqual([]);
  });

  it("tells a busy refusal apart from other refusals", () => {
    expect(isBusyRefusal(busyRefusal())).toBe(true);
    expect(isBusyRefusal(new ApiRequestError("This item is already listed on Depop.", 409, false))).toBe(false);
    expect(isBusyRefusal(new Error("marketplace_busy"))).toBe(false);
  });

  it("sends nothing while the marketplace is busy, then the confirmed revision once it is free", async () => {
    const send = vi.fn().mockResolvedValue({});
    const waitlist = new MarketplaceWaitlist(send);
    waitlist.add(item("a"));
    waitlist.setBusy(() => true);
    expect(send).not.toHaveBeenCalled();
    waitlist.setBusy(free);
    await settle();
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({ convId: "a", revisionId: "rev-a", kind: "list" });
    expect(waitlist.snapshot()).toEqual([]);
  });

  it("starts one action per marketplace at a time, in the order confirmed, beside other marketplaces", async () => {
    let finish!: () => void;
    const send = vi.fn((action: WaitingAction) => action.marketplace === "depop" && action.convId === "a"
      ? new Promise<void>((resolve) => { finish = resolve; }) : Promise.resolve());
    const waitlist = new MarketplaceWaitlist(send);
    waitlist.add(item("a"));
    waitlist.add(item("b"));
    waitlist.add(item("c", "mercari"));
    waitlist.setBusy(free);
    await settle();
    expect(send.mock.calls.map(([action]) => `${action.marketplace}:${action.convId}`)).toEqual(["depop:a", "mercari:c"]);
    finish();
    await settle();
    waitlist.setBusy(free);
    await settle();
    expect(send.mock.calls.map(([action]) => action.convId)).toEqual(["a", "c", "b"]);
  });

  it("keeps its place after a busy refusal and asks again only after the next read", async () => {
    const send = vi.fn().mockRejectedValueOnce(busyRefusal()).mockResolvedValue({});
    const waitlist = new MarketplaceWaitlist(send);
    waitlist.add(item("a"));
    waitlist.setBusy(free);
    await settle();
    expect(waitlist.snapshot()).toMatchObject([{ convId: "a", starting: false, error: null }]);
    waitlist.tick();
    await settle();
    expect(send).toHaveBeenCalledTimes(1);
    waitlist.setBusy(free);
    await settle();
    expect(send).toHaveBeenCalledTimes(2);
    expect(waitlist.snapshot()).toEqual([]);
  });

  it("stops on any other refusal and never retries it", async () => {
    const send = vi.fn().mockRejectedValue(new ApiRequestError("The listing changed. Refresh it before continuing.", 409, false));
    const failed = vi.fn();
    const waitlist = new MarketplaceWaitlist(send);
    waitlist.listener = { failed };
    waitlist.add(item("a"));
    waitlist.setBusy(free);
    await settle();
    waitlist.setBusy(free);
    await settle();
    expect(send).toHaveBeenCalledTimes(1);
    expect(waitlist.snapshot()).toMatchObject([{ convId: "a", error: "The listing changed. Refresh it before continuing." }]);
    expect(failed).toHaveBeenCalledTimes(1);
  });

  it("drops a cancelled action before its turn", async () => {
    const send = vi.fn().mockResolvedValue({});
    const waitlist = new MarketplaceWaitlist(send);
    waitlist.add(item("a"));
    waitlist.remove(waitlist.snapshot()[0].id);
    waitlist.setBusy(free);
    await settle();
    expect(send).not.toHaveBeenCalled();
  });

  it("reads which marketplaces open jobs hold", () => {
    const busy = busyFromJobs([{ current_step: "native_depop_publish" }]);
    expect(busy("depop")).toBe(true);
    expect(busy("mercari")).toBe(false);
    expect(busyFromJobs([{ current_step: "native_delist" }])("mercari")).toBe(true);
  });
});
