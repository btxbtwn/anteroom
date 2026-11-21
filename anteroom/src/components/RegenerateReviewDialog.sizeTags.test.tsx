// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";
import { api } from "../api/client";
import type { Conversation, ListingPhoto, SizeTagReadResult } from "../api/types";
import { RegenerateReviewDialog, type RegenerateReviewListing } from "./RegenerateReviewDialog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const listings: RegenerateReviewListing[] = [
  { id: "c-shorts", title: "Shorts" },
  { id: "c-jeans", title: "Jeans" },
  { id: "c-tee", title: "Tee" },
];

const tagReads: Record<string, SizeTagReadResult> = {
  "c-shorts": { photos_read: 2, status: "accepted", size: "0", sizes: ["0"] },
  "c-jeans": { photos_read: 2, status: "accepted", size: "32 x 30", sizes: ["32 x 30"] },
  "c-tee": { photos_read: 2, status: "not_found", size: null, sizes: [] },
};

function makePhotos(convId: string, count = 1): ListingPhoto[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `${convId}-p${i + 1}`,
    conversation_id: convId,
    original_filename: `${i + 1}.jpg`,
    mime_type: "image/jpeg",
    size_bytes: 100,
    display_order: i,
    width: 100,
    height: 100,
    created_at: "2026-11-20T00:00:00Z",
    url: `/api/conversations/${convId}/photos/${convId}-p${i + 1}/bytes?v=abc`,
  }));
}

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

async function openDialog({
  notesById = {},
  photoCount = 1,
  onRewrite = vi.fn(),
}: {
  notesById?: Record<string, string | null>;
  photoCount?: number;
  onRewrite?: () => void;
} = {}) {
  vi.spyOn(api.conversations, "regenerateDetails").mockImplementation(async (id) => ({
    notes: notesById[id] ?? null,
  }));
  vi.spyOn(api.conversations, "photos").mockImplementation(async (id) => makePhotos(id, photoCount));
  vi.spyOn(api.conversations, "get").mockImplementation(async (id) => ({
    id,
    title: listings.find((item) => item.id === id)?.title ?? "Listing",
    status: "draft",
    created_at: "2026-11-20T00:00:00Z",
    updated_at: "2026-11-20T00:00:00Z",
    notes: notesById[id] ?? null,
  } as Conversation));
  const update = vi.spyOn(api.conversations, "update").mockResolvedValue({} as Conversation);

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(
    <WorkspaceRuntimeProvider value={desktopRuntime}>
      <QueryClientProvider client={client}>
        <RegenerateReviewDialog listings={listings} onClose={vi.fn()} onRewrite={onRewrite} />
      </QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  ));
  const size = (title: string) => document.querySelector<HTMLInputElement>(`[aria-label="Size for ${title}"]`)!;
  const button = (name: string) => [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === name)!;
  await settle(() => expect(size("Shorts")).toBeTruthy());
  return { root, size, button, update, onRewrite };
}

it("loads saved tag sizes, reads blank tags from saved photos and saves edits before rewriting", async () => {
  const read = vi.spyOn(api.sizeTags, "readSaved").mockImplementation(async (id) => tagReads[id]!);
  const onRewrite = vi.fn();
  const { root, size, button, update } = await openDialog({
    notesById: {
      "c-shorts": JSON.stringify({ cog: "2.50" }),
      "c-jeans": JSON.stringify({ size: "34 x 30", sku: "J-1" }),
      "c-tee": JSON.stringify({ flaws: "pinhole" }),
    },
    onRewrite,
  });
  try {
    expect(size("Shorts").value).toBe("");
    expect(size("Jeans").value).toBe("34 x 30");
    expect(size("Tee").value).toBe("");

    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Shorts").value).toBe("0"));
    expect(read).toHaveBeenCalledTimes(3);
    expect(size("Shorts").className).toContain("is-suggested");
    expect(size("Jeans").value).toBe("34 x 30");
    expect(size("Jeans").className).toContain("needs-review");
    expect(document.body.textContent).toContain("The tag reads 32 x 30. Check the size you typed.");
    expect(size("Tee").value).toBe("");
    expect(size("Tee").className).toContain("needs-review");
    expect(document.getElementById(size("Tee").getAttribute("aria-describedby")!)!.textContent)
      .toBe("No size tag found in the photos. Type the size from the tag.");
    expect(document.querySelector('[role="status"]')!.textContent).toBe("Size tags read. 1 filled, 2 to check.");

    await act(async () => setInput(size("Tee"), "M"));
    expect(size("Tee").className).not.toContain("needs-review");

    expect(button("Rewrite 3").disabled).toBe(false);
    await act(async () => {
      document.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await settle(() => expect(onRewrite).toHaveBeenCalledTimes(1));

    // Only Shorts (filled 0) and Tee (typed M) changed; Jeans kept its saved 34 x 30.
    expect(update).toHaveBeenCalledTimes(2);
    const savedById = Object.fromEntries(
      update.mock.calls.map(([id, body]) => [id, JSON.parse((body as { notes: string }).notes)]),
    );
    expect(savedById["c-shorts"].size).toBe("0");
    expect(savedById["c-shorts"].cog).toBe("2.50");
    expect(savedById["c-tee"].size).toBe("M");
    expect(savedById["c-tee"].flaws).toBe("pinhole");
  } finally {
    await act(async () => root.unmount());
  }
});

it("clears a read size, voids a tag warning and drops an in-flight read when a saved photo is removed", async () => {
  const read = vi.spyOn(api.sizeTags, "readSaved").mockImplementation(async (id) => tagReads[id]!);
  vi.spyOn(api.conversations, "deletePhoto").mockResolvedValue({ ok: true });
  const { root, size, button } = await openDialog({
    notesById: { "c-jeans": JSON.stringify({ size: "34 x 30" }) },
    photoCount: 2,
  });
  try {
    await act(async () => button("Read size tags").click());
    await settle(() => expect(size("Shorts").value).toBe("0"));
    expect(size("Jeans").className).toContain("needs-review");

    // Remove photo 2 from Shorts (suggested) and Jeans (typed with warning).
    const removeButtons = document.querySelectorAll<HTMLButtonElement>('[aria-label="Remove photo 2"]');
    await act(async () => removeButtons[0]!.click());
    await settle(() => expect(size("Shorts").value).toBe(""));
    expect(size("Shorts").className).toContain("needs-review");
    expect(document.body.textContent).toContain("A photo was removed, so the size read from it was cleared.");

    await act(async () => removeButtons[1]!.click());
    await settle(() => expect(document.getElementById(size("Jeans").getAttribute("aria-describedby")!)!.textContent)
      .toBe("A photo was removed, so the earlier tag check no longer applies. Read size tags again or check the tag."));

    // In-flight read on Tee is dropped if a photo is removed while reading.
    const pending: Array<(result: SizeTagReadResult) => void> = [];
    read.mockImplementation(() => new Promise((resolve) => { pending.push(resolve); }));
    await act(async () => button("Read size tags").click());
    await settle(() => expect(pending).toHaveLength(3));
    await act(async () => document.querySelectorAll<HTMLButtonElement>('[aria-label="Remove photo 2"]')[2]!.click());
    await settle(() => expect(document.getElementById(size("Tee").getAttribute("aria-describedby")!)!.textContent)
      .toBe("A photo was removed while reading. Read size tags again or type the size."));

    const xl: SizeTagReadResult = { photos_read: 2, status: "accepted", size: "XL", sizes: ["XL"] };
    await act(async () => { for (const resolve of pending) resolve(xl); });
    await settle(() => expect(size("Shorts").value).toBe("XL"));
    expect(size("Tee").value).toBe("");
    expect(size("Tee").className).toContain("needs-review");
  } finally {
    await act(async () => root.unmount());
  }
});

it("keeps a size mismatch warning when a re-read fails or finds no tag", async () => {
  const read = vi.spyOn(api.sizeTags, "readSaved").mockImplementation(async (id) => tagReads[id]!);
  const { root, size, button } = await openDialog({
    notesById: { "c-jeans": JSON.stringify({ size: "34 x 30" }) },
  });
  const note = () => document.getElementById(size("Jeans").getAttribute("aria-describedby")!)!.textContent;
  try {
    await act(async () => button("Read size tags").click());
    await settle(() => expect(note()).toBe("The tag reads 32 x 30. Check the size you typed."));

    read.mockRejectedValue(new Error("OpenRouter stopped answering while looking at the photos."));
    await act(async () => button("Read size tags").click());
    await settle(() => expect(document.querySelector('[role="status"]')!.textContent).toContain("Size tags read."));
    expect(size("Jeans").className).toContain("needs-review");
    expect(note()).toBe("OpenRouter stopped answering while looking at the photos. The tag reads 32 x 30. Check the size you typed.");

    read.mockResolvedValue({ photos_read: 1, status: "not_found", size: null, sizes: [] });
    await act(async () => button("Read size tags").click());
    await settle(() => expect(document.querySelector('[role="status"]')!.textContent).toContain("Size tags read."));
    expect(size("Jeans").className).toContain("needs-review");
    expect(note()).toBe("The tag reads 32 x 30. Check the size you typed.");
  } finally {
    await act(async () => root.unmount());
  }
});
