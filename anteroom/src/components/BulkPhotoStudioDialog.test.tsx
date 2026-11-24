// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ListingPhoto } from "../api/types";
import { BulkPhotoBatches } from "./BulkPhotoStudioDialog";
import type { Listing } from "./sidebar/listingShelves";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./PriceDropParts", () => ({
  PriceDialog: ({ children, footer }: { children: ReactNode; footer: ReactNode }) => <>{children}{footer}</>,
}));

const photos: ListingPhoto[] = Array.from({ length: 21 }, (_, index) => ({
  id: `p${index}`, conversation_id: "draft", original_filename: `p${index}.jpg`, mime_type: "image/jpeg",
  size_bytes: 10, display_order: index, width: 100, height: 100, created_at: "", url: `/api/photos/p${index}?v=before`,
}));

async function openDialog() {
  vi.spyOn(api.settings, "photoroom").mockResolvedValue({ configured: true, masked_key: null, source: "sandbox", available: true });
  vi.spyOn(api.automations, "scheduledDrafts").mockResolvedValue([]);
  const client = new QueryClient();
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const button = (name: string) => [...host.querySelectorAll("button")].find((control) => control.textContent?.trim() === name);
  const click = async (name: string) => {
    await vi.waitFor(() => expect(button(name)?.disabled).toBe(false));
    await act(async () => { button(name)!.click(); });
  };
  const render = (batches: { id: number; listings: Listing[] }[]) => act(async () => root.render(<QueryClientProvider client={client}>
    <BulkPhotoBatches batches={batches} onDone={() => {}} />
  </QueryClientProvider>));
  await render([{ id: 1, listings: [{ id: "draft", status: "draft", title: "Jacket" }] }]);
  const close = async () => {
    await act(async () => root.unmount());
    client.clear();
    host.remove();
    vi.restoreAllMocks();
  };
  return { click, host, close, render };
}

it("splits a large selection and resumes failed or unsent photos without editing saved photos again", async () => {
  vi.spyOn(api.conversations, "photos").mockResolvedValue(photos);
  const studio = vi.spyOn(api.conversations, "photoStudio")
    .mockResolvedValueOnce({
      photos: [photos[0]],
      errors: photos.slice(1, 20).map((photo) => ({ photo_id: photo.id, detail: "No credits left." })),
      provider_blocked: true,
    })
    .mockResolvedValueOnce({ photos: photos.slice(1), errors: [] });
  const { click, host, close } = await openDialog();
  try {
    await click("Review photos");
    await click("All photos");
    await click("Apply to 21 photos");
    expect(studio.mock.calls.map((call) => call[1])).toEqual([photos.slice(0, 20).map((photo) => photo.id)]);
    expect(host.textContent).toContain("1 saved · 19 failed · 1 not sent");
    await click("Resume batch");
    expect(studio.mock.calls[1]?.[1]).toEqual(photos.slice(1).map((photo) => photo.id));
    expect(studio.mock.calls[1]?.[4]?.photo_versions.p20).toBe("before");
    expect(host.textContent).toContain("21 saved · 0 failed");
  } finally {
    await close();
  }
});

it("reselects only unchanged, unedited photos after a reply is lost", async () => {
  const [saved, sameVersion, changed, untouched] = photos;
  const edits = { studio: { key: "saved", options: { remove_background: true } } };
  vi.spyOn(api.conversations, "photos")
    .mockResolvedValueOnce([saved, sameVersion, changed, untouched])
    .mockResolvedValueOnce([
      { ...saved, url: "/api/photos/p0?v=saved", edits },
      { ...sameVersion, edits },
      { ...changed, url: "/api/photos/p2?v=changed" },
      untouched,
    ]);
  const studio = vi.spyOn(api.conversations, "photoStudio")
    .mockRejectedValueOnce(new TypeError("Network request failed"))
    .mockResolvedValueOnce({ photos: [untouched], errors: [] });
  const { click, host, close } = await openDialog();
  try {
    await click("Review photos");
    await click("All photos");
    await click("Apply to 4 photos");
    await click("Review again");
    await vi.waitFor(() => expect(host.textContent).toContain("1 selected across 1 draft"));
    await click("Apply to 1 photo");
    expect(studio.mock.calls.map((call) => call[1])).toEqual([
      [saved.id, sameVersion.id, changed.id, untouched.id],
      [untouched.id],
    ]);
  } finally {
    await close();
  }
});

it("starts an independent batch and skips photos the first batch is editing", async () => {
  vi.spyOn(api.conversations, "photos").mockImplementation(async (id) => [{ ...photos[0], id: `${id}-p0`, conversation_id: id }]);
  let finish: () => void = () => {};
  let firstRequest = true;
  const studio = vi.spyOn(api.conversations, "photoStudio").mockImplementation((id, ids) => {
    const response = { photos: ids.map((photoId) => ({ ...photos[0], id: photoId, conversation_id: id })), errors: [] };
    if (!firstRequest) return Promise.resolve(response);
    firstRequest = false;
    return new Promise((resolve) => { finish = () => resolve(response); });
  });
  const firstDraft = { id: "draft", status: "draft", title: "Jacket" };
  const { click, host, close, render } = await openDialog();
  try {
    await click("Review photos");
    await click("Apply to 1 photo");
    await click("Continue in background");
    await render([
      { id: 1, listings: [firstDraft] },
      { id: 2, listings: [firstDraft, { id: "other", status: "draft", title: "Boots" }] },
    ]);
    expect(host.textContent).toContain("Photo edits · 0 of 1");
    await click("Review photos");
    await click("Apply to 2 photos");
    await vi.waitFor(() => expect(host.textContent).toContain("1 saved · 0 failed · 1 skipped"));
    expect(studio.mock.calls.map((call) => call[0])).toEqual(["draft", "other"]);
    await act(async () => finish());
  } finally {
    await close();
  }
});
