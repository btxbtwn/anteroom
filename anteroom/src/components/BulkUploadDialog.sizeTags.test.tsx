// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";
import { api } from "../api/client";
import type { MeasurementReadResult, SizeTagReadResult } from "../api/types";
import type { BulkUploadItem } from "../bulkPhotoUpload";
import { BulkUploadDialog } from "./BulkUploadDialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const groups = ["Shorts", "Jeans", "Tee"].map((folder) => ({ folder, files: [new File(["x"], `${folder}.jpg`, { type: "image/jpeg" })] }));
const tagReads: Record<string, SizeTagReadResult> = {
  Shorts: { photos_read: 1, status: "accepted", size: "0", sizes: ["0"] },
  Jeans: { photos_read: 1, status: "accepted", size: "32 x 30", sizes: ["32 x 30"] },
  Tee: { photos_read: 1, status: "not_found", size: null, sizes: [] },
};

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

function setInput(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function settle(check: () => void) {
  await vi.waitFor(async () => {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    check();
  });
}

async function openDialog(onConfirm = vi.fn(), dialogGroups = groups) {
  vi.spyOn(api.boxes, "list").mockResolvedValue({ boxes: [] } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(
    <WorkspaceRuntimeProvider value={desktopRuntime}>
      <QueryClientProvider client={client}>
        <BulkUploadDialog groups={dialogGroups} onCancel={vi.fn()} onConfirm={onConfirm} />
      </QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  ));
  const size = (title: string) => document.querySelector<HTMLInputElement>(`[aria-label="Size for ${title}"]`)!;
  const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === name)!;
  return { root, size, button };
}

it("fills blank sizes from agreed tags, keeps typed ones and leaves unclear tags for the seller", async () => {
  const read = vi.spyOn(api.sizeTags, "read").mockImplementation(async (files) => tagReads[files[0]!.name.replace(".jpg", "")]!);
  const onConfirm = vi.fn();
  const { root, size, button } = await openDialog(onConfirm);
  try {
    await act(async () => setInput(size("Jeans"), "34 x 30"));
    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Shorts").value).toBe("0"));
    expect(read).toHaveBeenCalledTimes(3);

    // Size 0 is a size: filled and marked as read from the tag.
    expect(size("Shorts").className).toContain("is-suggested");
    // A typed size wins; a tag that disagrees asks the seller to check.
    expect(size("Jeans").value).toBe("34 x 30");
    expect(size("Jeans").className).toContain("needs-review");
    expect(document.body.textContent).toContain("The tag reads 32 x 30. Check the size you typed.");
    // No tag: nothing guessed, the seller is asked.
    expect(size("Tee").value).toBe("");
    expect(size("Tee").className).toContain("needs-review");
    const note = document.getElementById(size("Tee").getAttribute("aria-describedby")!)!;
    expect(note.textContent).toBe("No size tag found in the photos. Type the size from the tag.");

    // Typing a size clears the review mark.
    await act(async () => setInput(size("Tee"), "M"));
    expect(size("Tee").className).not.toContain("needs-review");

    await act(async () => button("Create drafts only").click());
    const items: BulkUploadItem[] = onConfirm.mock.calls[0]![1];
    expect(items.map((item) => item.size)).toEqual(["0", "34 x 30", "M"]);
  } finally {
    await act(async () => root.unmount());
  }
});

it("replaces an earlier tag reading, flags a failed read and announces once", async () => {
  const read = vi.spyOn(api.sizeTags, "read")
    .mockResolvedValueOnce(tagReads.Shorts!).mockResolvedValueOnce(tagReads.Jeans!).mockResolvedValueOnce(tagReads.Tee!);
  const { root, size, button } = await openDialog();
  try {
    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Jeans").value).toBe("32 x 30"));
    expect(document.querySelector('[role="status"]')!.textContent).toBe("Size tags read. 2 filled, 1 to check.");

    // A second read of the same photos: the earlier suggestion is not "typed", so a conflict clears it.
    read.mockReset();
    read.mockImplementation(async (files) => {
      if (files[0]!.name === "Tee.jpg") throw new Error("OpenRouter stopped answering while looking at the photos.");
      return { photos_read: 1, status: "conflict", size: null, sizes: ["0", "2"] };
    });
    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Shorts").value).toBe(""));
    expect(size("Shorts").className).toContain("needs-review");
    expect(size("Shorts").className).not.toContain("is-suggested");
    // A failed read on a blank size asks for it and names the provider that failed.
    expect(size("Tee").className).toContain("needs-review");
    expect(document.getElementById(size("Tee").getAttribute("aria-describedby")!)!.textContent)
      .toBe("OpenRouter stopped answering while looking at the photos. Type the size from the tag.");
    expect(document.querySelector('[role="status"]')!.textContent).toBe("Size tags read. 0 filled, 3 to check.");
  } finally {
    await act(async () => root.unmount());
  }
});

it("clears a tag size when its photos change and ignores a reading of the earlier photos", async () => {
  const pending: Array<(result: SizeTagReadResult) => void> = [];
  const read = vi.spyOn(api.sizeTags, "read")
    .mockResolvedValueOnce(tagReads.Shorts!).mockResolvedValueOnce(tagReads.Jeans!).mockResolvedValueOnce(tagReads.Tee!);
  const twoPhotos = groups.map((group) => ({ ...group, files: [...group.files, new File(["y"], `${group.folder}-2.jpg`, { type: "image/jpeg" })] }));
  vi.spyOn(api.boxes, "list").mockResolvedValue({ boxes: [] } as never);
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root.render(
    <WorkspaceRuntimeProvider value={desktopRuntime}>
      <QueryClientProvider client={client}>
        <BulkUploadDialog groups={twoPhotos} onCancel={vi.fn()} onConfirm={vi.fn()} />
      </QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  ));
  const size = (title: string) => document.querySelector<HTMLInputElement>(`[aria-label="Size for ${title}"]`)!;
  const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === name)!;
  try {
    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Shorts").value).toBe("0"));

    // Removing a photo clears the size it may have come from.
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Remove photo 2"]')!.click());
    expect(size("Shorts").value).toBe("");
    expect(size("Shorts").className).toContain("needs-review");
    expect(document.body.textContent).toContain("A photo was removed, so the size read from it was cleared.");

    // A reading still running when a photo is removed is not applied. Jeans (holding an earlier
    // suggestion) and Tee (blank) each lose a photo mid-read; Shorts keeps its photos and is the control.
    read.mockImplementation(() => new Promise((resolve) => { pending.push(resolve); }));
    await act(async () => button("Read size tags").click());
    expect(pending).toHaveLength(3);
    for (let removed = 0; removed < 2; removed++) {
      await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Remove photo 2"]')!.click());
    }
    const xl: SizeTagReadResult = { photos_read: 2, status: "accepted", size: "XL", sizes: ["XL"] };
    await act(async () => { for (const resolve of pending) resolve(xl); });
    await settle(() => expect(size("Shorts").value).toBe("XL"));
    expect(size("Jeans").value).toBe("");
    expect(size("Tee").value).toBe("");
    // The blank row's note says its reading was dropped, not "Reading…", and asks for the size.
    expect(size("Tee").className).toContain("needs-review");
    expect(document.getElementById(size("Tee").getAttribute("aria-describedby")!)!.textContent)
      .toBe("A photo was removed while reading. Read size tags again or type the size.");
  } finally {
    await act(async () => root.unmount());
  }
});

it("flags a size an earlier reading filled when a re-read fails", async () => {
  const read = vi.spyOn(api.sizeTags, "read").mockImplementation(async (files) => tagReads[files[0]!.name.replace(".jpg", "")]!);
  const { root, size, button } = await openDialog();
  try {
    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Shorts").value).toBe("0"));
    read.mockRejectedValue(new Error("OpenRouter stopped answering while looking at the photos."));
    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Shorts").className).toContain("needs-review"));
    expect(size("Shorts").value).toBe("0");
    expect(document.getElementById(size("Shorts").getAttribute("aria-describedby")!)!.textContent)
      .toBe("OpenRouter stopped answering while looking at the photos. Check the size against the tag.");
  } finally {
    await act(async () => root.unmount());
  }
});

it("keeps a size mismatch warning when a re-read finds no tag", async () => {
  const read = vi.spyOn(api.sizeTags, "read").mockImplementation(async (files) => tagReads[files[0]!.name.replace(".jpg", "")]!);
  const { root, size, button } = await openDialog();
  try {
    await act(async () => setInput(size("Jeans"), "34 x 30"));
    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Jeans").className).toContain("needs-review"));
    const note = () => document.getElementById(size("Jeans").getAttribute("aria-describedby")!)!.textContent;
    const reread = async (mock: () => void) => {
      read.mockClear();
      mock();
      await act(async () => button("Read size tags").click());
      // The status empties when a read starts, so this waits for this read, not the last one.
      await settle(() => expect(document.querySelector('[role="status"]')!.textContent).toContain("Size tags read."));
      expect(read).toHaveBeenCalledTimes(3);
    };
    // A failed re-read shows its error with the mismatch, without dropping it.
    await reread(() => read.mockRejectedValue(new Error("OpenRouter stopped answering while looking at the photos.")));
    expect(size("Jeans").className).toContain("needs-review");
    expect(note()).toBe("OpenRouter stopped answering while looking at the photos. The tag reads 32 x 30. Check the size you typed.");
    // A later re-read that finds no tag restores the mismatch itself, not the error.
    await reread(() => read.mockResolvedValue({ photos_read: 1, status: "not_found", size: null, sizes: [] }));
    expect(size("Jeans").value).toBe("34 x 30");
    expect(size("Jeans").className).toContain("needs-review");
    expect(note()).toBe("The tag reads 32 x 30. Check the size you typed.");
    // Editing the size clears the warning and its note at once; a later empty re-read doesn't bring it back.
    await act(async () => setInput(size("Jeans"), "32 x 30"));
    expect(size("Jeans").getAttribute("aria-describedby")).toBeNull();
    expect(size("Jeans").className).not.toContain("needs-review");
    await reread(() => read.mockResolvedValue({ photos_read: 1, status: "not_found", size: null, sizes: [] }));
    expect(size("Jeans").className).not.toContain("needs-review");
  } finally {
    await act(async () => root.unmount());
  }
});

it("drops a typed size's tag warning when a photo is removed, so an empty re-read can't restore it", async () => {
  const read = vi.spyOn(api.sizeTags, "read").mockImplementation(async (files) => tagReads[files[0]!.name.replace(".jpg", "")]!);
  const twoPhotos = groups.map((group) => ({ ...group, files: [...group.files, new File(["y"], `${group.folder}-2.jpg`, { type: "image/jpeg" })] }));
  const { root, size, button } = await openDialog(vi.fn(), twoPhotos);
  const note = () => document.getElementById(size("Jeans").getAttribute("aria-describedby")!)!.textContent;
  try {
    await act(async () => setInput(size("Jeans"), "34 x 30"));
    await act(async () => button("Read size tags").click());
    await settle(() => expect(note()).toBe("The tag reads 32 x 30. Check the size you typed."));
    // Jeans is the second item; its "Remove photo 2" is the second such button.
    await act(async () => document.querySelectorAll<HTMLButtonElement>('[aria-label="Remove photo 2"]')[1]!.click());
    expect(size("Jeans").value).toBe("34 x 30");
    expect(size("Jeans").className).toContain("needs-review");
    expect(note()).toBe("A photo was removed, so the earlier tag check no longer applies. Read size tags again or check the tag.");
    read.mockResolvedValue({ photos_read: 1, status: "not_found", size: null, sizes: [] });
    await act(async () => button("Read size tags").click());
    await settle(() => expect(document.querySelector('[role="status"]')!.textContent).toContain("Size tags read."));
    expect(size("Jeans").className).not.toContain("needs-review");
    expect(size("Jeans").getAttribute("aria-describedby")).toBeNull();
  } finally {
    await act(async () => root.unmount());
  }
});

it("never shows the provider or model in reading notes", async () => {
  vi.spyOn(api.sizeTags, "read").mockResolvedValue(tagReads.Shorts!);
  // An older server still sends who read; the dialog must not show it.
  vi.spyOn(api.measurements, "read").mockResolvedValue({
    photos_read: 1,
    reader: "OpenRouter (google/gemini-flash-latest)",
    measurements: { length: { status: "accepted", value: 27, reads: [27, 27] } },
  } as MeasurementReadResult);
  const { root, button } = await openDialog();
  try {
    await act(async () => button("Read size tags").click());
    await act(async () => button("Read measurements").click());
    await settle(() => expect(document.body.textContent).toContain("Filled from the photos. Check them."));
    expect(document.body.textContent).toContain("Size filled from the tag. Check it.");
    expect(document.body.textContent).not.toMatch(/openrouter|gemini|google/i);
  } finally {
    await act(async () => root.unmount());
  }
});

it("paces hosted bulk reads to two in flight and aborts them when the dialog unmounts", async () => {
  const fiveGroups = ["Item 1", "Item 2", "Item 3", "Item 4", "Item 5"].map((folder) => ({
    folder,
    files: [new File(["x"], `${folder}.jpg`, { type: "image/jpeg" })],
  }));
  const hostedRuntime = { ...desktopRuntime, hosted: true };
  vi.spyOn(api.boxes, "list").mockResolvedValue({ boxes: [] } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(
    <WorkspaceRuntimeProvider value={hostedRuntime}>
      <QueryClientProvider client={client}>
        <BulkUploadDialog groups={fiveGroups} onCancel={vi.fn()} onConfirm={vi.fn()} />
      </QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  ));
  const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === name)!;

  let active = 0;
  let peak = 0;
  const signals: Array<AbortSignal | undefined> = [];
  const pendingReads: Array<() => void> = [];
  vi.spyOn(api.measurements, "read").mockImplementation((_files, _garment, signal) => {
    signals.push(signal);
    active += 1;
    peak = Math.max(peak, active);
    return new Promise((resolve) => {
      pendingReads.push(() => {
        active -= 1;
        resolve({ photos_read: 1, measurements: { length: { status: "accepted", value: 27, reads: [27, 27] } } });
      });
    });
  });

  await act(async () => button("Read measurements").click());
  expect(signals).toHaveLength(2);
  expect(peak).toBe(2);

  while (pendingReads.length) {
    const finish = pendingReads.shift()!;
    await act(async () => { finish(); });
  }
  expect(signals).toHaveLength(5);
  expect(peak).toBe(2);

  // Now start a hosted size-tag read, confirm only 2 start initially, and unmount mid-read.
  const tagSignals: Array<AbortSignal | undefined> = [];
  vi.spyOn(api.sizeTags, "read").mockImplementation((_files, signal) => {
    tagSignals.push(signal);
    return new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    });
  });

  await act(async () => button("Read size tags").click());
  expect(tagSignals).toHaveLength(2);
  expect(tagSignals.every((signal) => signal && !signal.aborted)).toBe(true);

  await act(async () => root.unmount());
  expect(tagSignals.every((signal) => signal?.aborted)).toBe(true);
  // Remaining 3 queued items never start after unmount.
  expect(tagSignals).toHaveLength(2);
});
