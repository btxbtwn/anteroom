// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ListingPhoto } from "../api/types";
import { PhotoEditorDialog } from "./PhotoEditorDialog";
import { toastManager } from "./ui/toast";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Dialogs render into a portal; render their content in place.
vi.mock("./ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogPopup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

// Base UI's checkbox needs a real browser's pointer events; a plain toggle stands in.
vi.mock("./ui/checkbox", () => ({
  Checkbox: ({ checked, disabled, onCheckedChange, "aria-label": label }: {
    checked?: boolean; disabled?: boolean; onCheckedChange?: (checked: boolean) => void; "aria-label"?: string;
  }) => (
    <button type="button" role="checkbox" aria-checked={Boolean(checked)} aria-label={label} disabled={disabled}
      onClick={(event) => { event.preventDefault(); onCheckedChange?.(!checked); }} />
  ),
}));

const photo: ListingPhoto = {
  id: "p1", conversation_id: "item", original_filename: "front.jpg", mime_type: "image/jpeg", size_bytes: 10,
  display_order: 0, width: 400, height: 300, created_at: "", url: "/api/photos/p1?v=abc",
  edits: { rotate: 90, straighten: 0, crop: null, exposure: 0, brightness: 0, contrast: 0, background: null },
};

let created = 0;
beforeEach(() => {
  created = 0;
  // Spies, so restoreAllMocks puts the real methods back on URL.
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:preview-${++created}`);
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
});
afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ""; });

const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 250)); });
const preview = () => document.body.querySelector<HTMLImageElement>('img[alt="Photo 1 crop preview"]');
const failure = () => document.body.querySelector<HTMLElement>("[data-photo-load-status]");
const cropFrame = () => document.body.querySelector("[data-crop-frame]");

it("keeps the displayed preview URL valid while a crop update is pending", async () => {
  vi.spyOn(api.conversations, "previewPhotoEdits").mockImplementation(async () => new Blob(["jpeg"]));
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<PhotoEditorDialog convId="item" photo={photo} index={0} onSaved={vi.fn()} onClose={vi.fn()} />));
  try {
    await settle();
    expect(preview()?.getAttribute("src")).toBe("blob:preview-1");
    const handle = host.querySelector('[aria-label="Resize from bottom right"]')!;
    await act(async () => {
      handle.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", shiftKey: true, bubbles: true }));
    });
    expect(preview()?.getAttribute("src")).toBe("blob:preview-1");
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    await settle();
    expect(preview()?.getAttribute("src")).toBe("blob:preview-2");
    expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:preview-1");
  } finally { await act(async () => root.unmount()); }
  expect(URL.revokeObjectURL).toHaveBeenLastCalledWith("blob:preview-2");
});

it("shows Retry above the crop frame when a preview cannot be shown, and Retry asks for a new preview", async () => {
  const previews = vi.spyOn(api.conversations, "previewPhotoEdits").mockResolvedValue(new Blob(["jpeg"]));
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<PhotoEditorDialog convId="item" photo={photo} index={0} onSaved={vi.fn()} onClose={vi.fn()} />));
  try {
    await settle();
    expect(preview()?.getAttribute("src")).toBe("blob:preview-1");
    expect(cropFrame()).not.toBeNull();

    // The browser could not decode the preview it was given.
    await act(async () => { preview()!.dispatchEvent(new Event("error")); });
    expect(failure()?.textContent).toContain("Couldn't load this photo.");
    // Nothing covers the Retry button.
    expect(cropFrame()).toBeNull();
    expect(previews).toHaveBeenCalledTimes(1);

    const retry = [...document.body.querySelectorAll("button")].find((button) => button.textContent === "Retry")!;
    await act(async () => { retry.click(); });
    await settle();
    // A new preview of the same edits, not the unreadable one again.
    expect(previews).toHaveBeenCalledTimes(2);
    expect(previews.mock.calls[1]?.slice(0, 4)).toEqual(previews.mock.calls[0]?.slice(0, 4));
    expect(failure()).toBeNull();
    expect(preview()?.getAttribute("src")).toBe("blob:preview-2");
    expect(cropFrame()).not.toBeNull();
  } finally { await act(async () => root.unmount()); }
});

const second: ListingPhoto = { ...photo, id: "p2", url: "/api/photos/p2?v=def", edits: null };
const buttonNamed = (name: string) => [...document.body.querySelectorAll("button")].find((button) => button.textContent?.trim() === name);
const checkboxNamed = (name: string) => [...document.body.querySelectorAll("label")]
  .find((label) => label.textContent?.startsWith(name))?.querySelector<HTMLElement>("[role=checkbox]");
const isChecked = (name: string) => checkboxNamed(name)?.getAttribute("aria-checked") === "true";

it("lets the seller leave AI Studio while a photo is still editing", async () => {
  vi.spyOn(api.conversations, "previewPhotoEdits").mockResolvedValue(new Blob(["jpeg"]));
  vi.spyOn(api.settings, "photoroom").mockResolvedValue({ configured: true, masked_key: null, source: "seller", available: true });
  const studio = vi.spyOn(api.conversations, "photoStudio");
  let finish!: (result: Awaited<ReturnType<typeof api.conversations.photoStudio>>) => void;
  const other = { ...second, conversation_id: "another-draft" };
  studio.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce({ photos: [other], errors: [] });
  const toast = vi.spyOn(toastManager, "add").mockReturnValue("studio-toast");
  const onSaved = vi.fn();
  const onClose = vi.fn();
  const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
  const client = new QueryClient();
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <PhotoEditorDialog key="item" convId="item" photo={photo} index={0} initialTab="studio" onSaved={onSaved} onClose={onClose} />
    </QueryClientProvider>,
  ));
  try {
    await act(async () => { buttonNamed("Apply")!.click(); });
    expect(studio).toHaveBeenCalledTimes(1);
    expect(toast).not.toHaveBeenCalled();
    await act(async () => { buttonNamed("Continue in background")!.click(); });
    expect(onClose).toHaveBeenCalledWith(true);
    const onOtherSaved = vi.fn();
    const onOtherClose = vi.fn();
    await act(async () => root.render(
      <QueryClientProvider client={client}>
        <PhotoEditorDialog key="other" convId="another-draft" photo={other} index={0} initialTab="studio"
          onSaved={onOtherSaved} onClose={onOtherClose} />
      </QueryClientProvider>,
    ));
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ type: "loading" }));
    await act(async () => { buttonNamed("Apply")!.click(); });
    expect(studio.mock.calls.map(([id]) => id)).toEqual(["item", "another-draft"]);
    expect(onOtherSaved).toHaveBeenCalledWith(other);
    expect(onSaved).not.toHaveBeenCalled();
    const saved = { ...photo, url: "/api/photos/p1?v=after" };
    await act(async () => { finish({ photos: [saved], errors: [] }); });
    expect(onSaved).toHaveBeenCalledWith(saved);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onOtherClose).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ type: "success" }));
  } finally {
    await act(async () => root.unmount());
    client.clear();
  }
});

it("applies AI Studio to the chosen photos and reports each photo that failed", async () => {
  vi.spyOn(api.conversations, "previewPhotoEdits").mockResolvedValue(new Blob(["jpeg"]));
  vi.spyOn(api.settings, "photoroom").mockResolvedValue({ configured: true, masked_key: "sk_pr_12...abcd", source: "seller", available: true });
  const studio = vi.spyOn(api.conversations, "photoStudio").mockResolvedValue({
    photos: [{ ...photo, edits: { studio: { key: "k", options: { remove_background: true } } } }],
    errors: [{ photo_id: "p2", detail: "Your Photoroom plan has no credits left for this edit." }],
  });
  const onSaved = vi.fn();
  const onClose = vi.fn();
  const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <PhotoEditorDialog convId="item" photo={photo} index={0} photos={[photo, second]} initialTab="studio" onSaved={onSaved} onClose={onClose} />
    </QueryClientProvider>,
  ));
  try {
    await settle();
    // Ghost mannequin and Flat lay exclude each other.
    await act(async () => { checkboxNamed("Ghost mannequin")!.click(); });
    expect(isChecked("Ghost mannequin")).toBe(true);
    await act(async () => { checkboxNamed("Flat lay")!.click(); });
    expect(isChecked("Flat lay")).toBe(true);
    expect(isChecked("Ghost mannequin")).toBe(false);
    await act(async () => { buttonNamed("Select all photos")!.click(); });
    await act(async () => { buttonNamed("Apply to 2 photos")!.click(); });
    await settle();
    expect(studio).toHaveBeenCalledTimes(1);
    const [, ids, options] = studio.mock.calls[0]!;
    expect(ids).toEqual(["p1", "p2"]);
    expect(options).toMatchObject({ flat_lay: true, ghost_mannequin: false, remove_background: true });
    expect(onSaved).toHaveBeenCalledTimes(1);
    // MarketplaceMedia refreshes asynchronously: the open photo prop is still the old recipe.
    expect(buttonNamed("Discard changes")).toBeUndefined();
    expect(buttonNamed("Apply")?.disabled).toBe(false);
    await act(async () => { buttonNamed("Apply")!.click(); });
    expect(studio.mock.calls[1]?.[1]).toEqual(["p2"]);
    // A failed photo keeps the editor open and says why.
    expect(onClose).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("Photo 2: Your Photoroom plan has no credits left for this edit.");
  } finally { await act(async () => root.unmount()); }
});

it("previews markup without the marks, which it draws over the photo itself", async () => {
  const edited: ListingPhoto = { ...photo, edits: { annotations: [{ kind: "arrow", x1: 0, y1: 0, x2: 1, y2: 1 }], border: { width: 0.1 } } };
  const previews = vi.spyOn(api.conversations, "previewPhotoEdits").mockResolvedValue(new Blob(["jpeg"]));
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<PhotoEditorDialog convId="item" photo={edited} index={0} initialTab="markup" onSaved={vi.fn()} onClose={vi.fn()} />));
  try {
    await settle();
    const [, , sent, crop] = previews.mock.calls[0]!;
    expect(crop).toBe(true);
    expect(sent).toMatchObject({ annotations: [], redactions: [], border: null });
    expect(document.body.querySelector("[data-markup-frame] line")).not.toBeNull();
    expect(document.body.textContent).toContain("Arrow 1");
  } finally { await act(async () => root.unmount()); }
});

it("restores hidden and partially clipped redactions when the crop is cleared", async () => {
  const redactions = [
    { x: 0.75, y: 0.25, width: 0.125, height: 0.25, style: "pixelate" as const },
    { x: 0.55, y: 0.25, width: 0.125, height: 0.25, style: "blur" as const },
  ];
  const edited: ListingPhoto = { ...photo, edits: { redactions } };
  const previews = vi.spyOn(api.conversations, "previewPhotoEdits").mockResolvedValue(new Blob(["jpeg"]));
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<PhotoEditorDialog convId="item" photo={edited} index={0} onSaved={vi.fn()} onClose={vi.fn()} />));
  try {
    await settle();
    const handle = host.querySelector('[aria-label="Resize from bottom right"]')!;
    for (let i = 0; i < 8; i++) {
      await act(async () => {
        handle.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", shiftKey: true, bubbles: true }));
      });
    }
    await settle();
    const cropped = previews.mock.lastCall?.[2];
    expect(cropped?.redactions).toHaveLength(1);
    expect(cropped?.redactions?.[0]?.style).toBe("blur");
    expect(cropped?.redactions?.[0]?.width).toBeCloseTo(1 / 12);
    await act(async () => { buttonNamed("Clear crop")!.click(); });
    await settle();
    expect(previews.mock.lastCall?.[2].redactions).toEqual(redactions);
  } finally { await act(async () => root.unmount()); }
});
