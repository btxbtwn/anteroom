// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ListingPhoto } from "../api/types";
import { PhotoEditor } from "./PhotoEditor";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Dialogs render into a portal; render their content in place.
vi.mock("./ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogPopup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

const photo: ListingPhoto = {
  id: "p1", conversation_id: "item", original_filename: "front.jpg", mime_type: "image/jpeg", size_bytes: 10,
  display_order: 0, width: 400, height: 300, created_at: "", url: "/api/photos/p1?v=abc",
  edits: { rotate: 90, straighten: 0, crop: null, exposure: 0, brightness: 0, contrast: 0, background: null },
};

let created = 0;
beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => `blob:preview-${++created}`, revokeObjectURL: vi.fn() }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = ""; });

const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 250)); });
const preview = () => document.body.querySelector<HTMLImageElement>('img[alt="ListingPhoto 1 with edits"]');
const failure = () => document.body.querySelector<HTMLElement>("[data-photo-load-status]");
const cropFrame = () => document.body.querySelector("[data-crop-frame]");

it("shows Retry above the crop frame when a preview cannot be shown, and Retry asks for a new preview", async () => {
  const previews = vi.spyOn(api.conversations, "previewPhotoEdits").mockResolvedValue(new Blob(["jpeg"]));
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<PhotoEditor convId="item" photo={photo} index={0} onSaved={vi.fn()} onClose={vi.fn()} />));
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
