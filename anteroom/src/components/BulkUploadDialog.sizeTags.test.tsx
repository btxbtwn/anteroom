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

it("paces hosted uploads without limiting concurrent model reads, cancels queued reads on photo removal, and aborts on unmount", async () => {
  const { installHostedApiSession } = await import("../api/transport");
  const requests: FakeDialogXhr[] = [];
  class FakeDialogXhr {
    url = "";
    status = 0;
    statusText = "";
    responseText = "";
    readyState = 0;
    aborted = false;
    sentFiles: string[] = [];
    private uploadLoadend: (() => void) | null = null;
    upload = {
      addEventListener: (event: string, cb: () => void) => {
        if (event === "loadend") this.uploadLoadend = cb;
      },
    };
    onreadystatechange: (() => void) | null = null;
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    ontimeout: (() => void) | null = null;
    onabort: (() => void) | null = null;
    constructor() { requests.push(this); }
    open(_method: string, url: string) { this.url = url; }
    setRequestHeader() {}
    getAllResponseHeaders() { return "Content-Type: application/json\r\n"; }
    send(body: FormData) {
      this.sentFiles = body.getAll("files").map((entry) => (entry as File).name);
    }
    finishUpload() { this.uploadLoadend?.(); }
    respond(status: number, body: unknown) {
      this.status = status;
      this.responseText = JSON.stringify(body);
      this.readyState = 4;
      this.onreadystatechange?.();
      this.onload?.();
    }
    abort() { this.aborted = true; this.onabort?.(); }
  }

  vi.stubGlobal("XMLHttpRequest", FakeDialogXhr);
  const uninstall = installHostedApiSession({
    seller: "seller-1",
    origin: "https://anteroom.example",
    accessToken: async () => "token",
    unauthorized: vi.fn(),
  });
  const threeGroups = ["Item 1", "Item 2", "Item 3"].map((folder) => ({
    folder,
    files: [
      new File(["a"], `${folder}-1.jpg`, { type: "image/jpeg" }),
      new File(["b"], `${folder}-2.jpg`, { type: "image/jpeg" }),
    ],
  }));
  const hostedRuntime = { ...desktopRuntime, hosted: true };
  vi.spyOn(api.boxes, "list").mockResolvedValue({ boxes: [] } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(
      <WorkspaceRuntimeProvider value={hostedRuntime}>
        <QueryClientProvider client={client}>
          <BulkUploadDialog groups={threeGroups} onCancel={vi.fn()} onConfirm={vi.fn()} />
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    ));
    const size = (title: string) => document.querySelector<HTMLInputElement>(`[aria-label="Size for ${title}"]`)!;
    const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === name)!;

    // Start a hosted size-tag batch of 3 items: only Item 1 starts uploading initially.
    await act(async () => button("Read size tags").click());
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]!.sentFiles).toEqual(["Item 1-1.jpg", "Item 1-2.jpg"]);

    // While Item 3 is still queued waiting to upload, remove photo 2 from Item 3.
    const removeButtons = document.querySelectorAll<HTMLButtonElement>('[aria-label="Remove photo 2"]');
    expect(removeButtons).toHaveLength(3);
    await act(async () => removeButtons[2]!.click());
    expect(document.getElementById(size("Item 3").getAttribute("aria-describedby")!)!.textContent)
      .toBe("A photo was removed while reading. Read size tags again or type the size.");

    // Item 1 finishes uploading while its model read is still pending: Item 2 immediately starts uploading
    // so both model reads run concurrently, and cancelled Item 3 never uploads its old photos.
    await act(async () => { requests[0]!.finishUpload(); });
    await vi.waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1]!.sentFiles).toEqual(["Item 2-1.jpg", "Item 2-2.jpg"]);
    await act(async () => { requests[1]!.finishUpload(); });
    expect(requests).toHaveLength(2);

    // Resolve both concurrent model reads; batch completes and Item 3 keeps its photo-removed note.
    await act(async () => {
      requests[0]!.respond(200, { photos_read: 2, status: "accepted", size: "M", sizes: ["M"] });
      requests[1]!.respond(200, { photos_read: 2, status: "accepted", size: "L", sizes: ["L"] });
    });
    await settle(() => expect(size("Item 2").value).toBe("L"));
    expect(size("Item 1").value).toBe("M");
    expect(size("Item 3").value).toBe("");
    expect(document.getElementById(size("Item 3").getAttribute("aria-describedby")!)!.textContent)
      .toBe("A photo was removed while reading. Read size tags again or type the size.");
    expect(button("Read size tags").disabled).toBe(false);
    expect(button("Create drafts only").disabled).toBe(false);

    // Now start hosted measurement reads across all 3 items, remove photo 2 from queued Item 2,
    // verify Item 1 & Item 3 overlap model reads while Item 2 never uploads, then unmount mid-read.
    requests.length = 0;
    await act(async () => button("Read measurements").click());
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]!.sentFiles).toEqual(["Item 1-1.jpg", "Item 1-2.jpg"]);

    // Item 3 only has 1 photo left, so Remove photo 2 buttons belong to Item 1 (index 0) and Item 2 (index 1).
    const measureRemoves = document.querySelectorAll<HTMLButtonElement>('[aria-label="Remove photo 2"]');
    expect(measureRemoves).toHaveLength(2);
    await act(async () => measureRemoves[1]!.click());
    expect(document.body.textContent).toContain("A photo was removed while reading. Read measurements again or measure by hand.");

    await act(async () => { requests[0]!.finishUpload(); });
    await vi.waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1]!.sentFiles).toEqual(["Item 3-1.jpg"]);
    await act(async () => { requests[1]!.finishUpload(); });
    expect(requests).toHaveLength(2);

    // Unmounting aborts both concurrent in-flight model reads.
    await act(async () => root.unmount());
    expect(requests[0]!.aborted).toBe(true);
    expect(requests[1]!.aborted).toBe(true);
  } finally {
    uninstall();
    vi.unstubAllGlobals();
  }
});
