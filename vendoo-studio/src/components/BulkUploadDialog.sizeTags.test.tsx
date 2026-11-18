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

async function openDialog(onConfirm = vi.fn()) {
  vi.spyOn(api.boxes, "list").mockResolvedValue({ boxes: [] } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(
    <WorkspaceRuntimeProvider value={desktopRuntime}>
      <QueryClientProvider client={client}>
        <BulkUploadDialog groups={groups} onCancel={vi.fn()} onConfirm={onConfirm} />
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
