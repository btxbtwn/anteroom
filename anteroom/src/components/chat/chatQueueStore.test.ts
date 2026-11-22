// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  chatQueue,
  editChatMessage,
  enqueueChatMessage,
  newQueuedChatMessage,
  removeChatMessage,
  resetChatQueuesForTest,
  assignChatThread,
  resolveFollowUp,
  takeNextChatMessage,
} from "./chatQueueStore";

beforeEach(() => {
  sessionStorage.clear();
  resetChatQueuesForTest();
});

describe("chat queue store", () => {
  it("keeps messages in submission order and hands each to one caller", () => {
    enqueueChatMessage("k", newQueuedChatMessage("first"));
    enqueueChatMessage("k", newQueuedChatMessage("second"));
    expect(takeNextChatMessage("k")?.text).toBe("first");
    expect(chatQueue("k").map((m) => m.text)).toEqual(["second"]);
    expect(takeNextChatMessage("other")).toBeNull();
  });

  it("removes and edits in place", () => {
    const first = newQueuedChatMessage("first");
    const second = newQueuedChatMessage("second");
    enqueueChatMessage("k", first);
    enqueueChatMessage("k", second);
    editChatMessage("k", first.id, "first, edited");
    removeChatMessage("k", second.id);
    expect(chatQueue("k").map((m) => m.text)).toEqual(["first, edited"]);
  });

  it("takes an in-memory message even when storage reads and writes throw", () => {
    const blocked = () => { throw new Error("Storage unavailable"); };
    vi.stubGlobal("sessionStorage", { getItem: blocked, setItem: blocked, removeItem: blocked });
    try {
      enqueueChatMessage("k", newQueuedChatMessage("still here"));
      expect(takeNextChatMessage("k")?.text).toBe("still here");
      expect(takeNextChatMessage("k")).toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("saves text across a reload but never images", () => {
    enqueueChatMessage("k", newQueuedChatMessage("text only", { threadId: "t" }));
    enqueueChatMessage("k", newQueuedChatMessage("with image", { payload: { attachments: [{ data: "…" }] } }));
    expect(sessionStorage.getItem("anteroom.chatQueue.v1")).not.toContain("with image");
    resetChatQueuesForTest();
    expect(chatQueue("k").map((m) => [m.text, m.threadId])).toEqual([["text only", "t"]]);
  });

  it("only matching messages leave with a filter", () => {
    enqueueChatMessage("k", newQueuedChatMessage("elsewhere", { threadId: "a" }));
    enqueueChatMessage("k", newQueuedChatMessage("here", { threadId: "b" }));
    expect(takeNextChatMessage("k", (m) => m.threadId === "b")?.text).toBe("here");
    expect(chatQueue("k").map((m) => m.text)).toEqual(["elsewhere"]);
  });

  it("steers by default while a run takes it, queues with the modifier or when it cannot", () => {
    expect(resolveFollowUp({ busy: false, canSteer: false, alternate: false })).toBe("send");
    expect(resolveFollowUp({ busy: true, canSteer: true, alternate: false })).toBe("steer");
    expect(resolveFollowUp({ busy: true, canSteer: true, alternate: true })).toBe("queue");
    expect(resolveFollowUp({ busy: true, canSteer: false, alternate: false })).toBe("queue");
    expect(resolveFollowUp({ busy: true, canSteer: false, alternate: true })).toBe("queue");
  });

  it("drops a saved message older than a day instead of sending it out of the blue", () => {
    const old = { id: "old", text: "stale", createdAt: new Date(Date.now() - 2 * 24 * 3600_000).toISOString() };
    const fresh = { id: "new", text: "fresh", createdAt: new Date().toISOString() };
    sessionStorage.setItem("anteroom.chatQueue.v1", JSON.stringify({ k: [old, fresh] }));
    expect(chatQueue("k").map((m) => m.text)).toEqual(["fresh"]);
  });

  it("keeps a question asked before its conversation existed in memory, then files it there", () => {
    enqueueChatMessage("assistant", newQueuedChatMessage("before the thread", { threadId: null }));
    expect(sessionStorage.getItem("anteroom.chatQueue.v1")).toBeNull();
    assignChatThread("assistant", "t1");
    expect(chatQueue("assistant").map((m) => m.threadId)).toEqual(["t1"]);
    resetChatQueuesForTest();
    expect(chatQueue("assistant").map((m) => [m.text, m.threadId])).toEqual([["before the thread", "t1"]]);
  });
});
