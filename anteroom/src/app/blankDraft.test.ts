import { afterEach, describe, expect, it, vi } from "vitest";
import { notifyUnsavedWork, registerUnsavedWork } from "../lib/unsavedWork";
import { trackPhotoUpload } from "../photoUploads";
import { discardBlankDraft, discardBlankDraftOnExit } from "./blankDraft";

const cleanups: Array<() => void> = [];
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => cleanup());
});

describe("discardBlankDraft", () => {
  it("asks the server to discard once nothing is pending", async () => {
    const discard = vi.fn(async () => ({ discarded: true }));
    await expect(discardBlankDraft("new", discard, { pollMs: 1 })).resolves.toBe(true);
    expect(discard).toHaveBeenCalledWith("new");
  });

  it("commits a typed field first and waits for its save to land", async () => {
    let saving = false;
    const order: string[] = [];
    cleanups.push(registerUnsavedWork({
      pending: () => saving,
      commit: () => {
        saving = true;
        order.push("commit");
        setTimeout(() => { saving = false; order.push("saved"); notifyUnsavedWork(); }, 5);
      },
    }));
    const discard = vi.fn(async () => {
      order.push("discard");
      return { discarded: false };
    });
    await expect(discardBlankDraft("new", discard, { pollMs: 1 })).resolves.toBe(false);
    expect(order).toEqual(["commit", "saved", "discard"]);
  });

  it("waits for a photo upload to that draft", async () => {
    let finish!: () => void;
    const upload = trackPhotoUpload("new", () => new Promise<void>((resolve) => { finish = resolve; }));
    const discard = vi.fn(async () => ({ discarded: false }));
    const done = discardBlankDraft("new", discard, { pollMs: 1 });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(discard).not.toHaveBeenCalled();
    finish();
    await upload;
    await done;
    expect(discard).toHaveBeenCalledOnce();
  });

  it("keeps the draft when a save never lands before the deadline", async () => {
    cleanups.push(registerUnsavedWork({ pending: () => true }));
    const discard = vi.fn(async () => ({ discarded: true }));
    await expect(discardBlankDraft("new", discard, { pollMs: 1, deadlineMs: 5 })).resolves.toBe(false);
    expect(discard).not.toHaveBeenCalled();
  });

  it("keeps the draft when the request fails", async () => {
    const discard = vi.fn(async () => { throw new Error("offline"); });
    await expect(discardBlankDraft("new", discard, { pollMs: 1 })).resolves.toBe(false);
  });
});

describe("discardBlankDraftOnExit", () => {
  it("discards at once when nothing is pending", () => {
    const discard = vi.fn(async () => ({ discarded: true }));
    expect(discardBlankDraftOnExit("new", discard)).toBe(true);
    expect(discard).toHaveBeenCalledWith("new");
  });

  it("leaves the draft alone while a save is pending", () => {
    cleanups.push(registerUnsavedWork({ pending: () => true }));
    const discard = vi.fn(async () => ({ discarded: true }));
    expect(discardBlankDraftOnExit("new", discard)).toBe(false);
    expect(discard).not.toHaveBeenCalled();
  });
});
