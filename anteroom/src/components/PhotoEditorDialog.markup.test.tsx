// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ListingPhoto } from "../api/types";
import { PhotoEditorDialog } from "./PhotoEditorDialog";

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
  edits: { rotate: 0, straighten: 0, crop: null, exposure: 0, brightness: 0, contrast: 0, background: null },
};

// The markup frame is 200 x 100 pixels at the page's origin.
const FRAME = { x: 0, y: 0, left: 0, top: 0, right: 200, bottom: 100, width: 200, height: 100, toJSON: () => ({}) };

let root: Root;
let host: HTMLDivElement;
beforeEach(async () => {
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => "blob:preview");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  vi.spyOn(api.conversations, "previewPhotoEdits").mockResolvedValue(new Blob(["jpeg"]));
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return (this.hasAttribute("data-markup-frame") ? FRAME : { ...FRAME, width: 0, height: 0 }) as DOMRect;
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(
    <PhotoEditorDialog convId="item" photo={photo} index={0} onSaved={vi.fn()} onClose={vi.fn()} initialTab="markup" />,
  ));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 250)); });
  await act(async () => { button("Pixelate").click(); });
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

const button = (name: string) => [...host.querySelectorAll("button")].find((item) => item.textContent === name)!;
const frame = () => host.querySelector<HTMLElement>("[data-markup-frame]")!;
const pointer = (type: string, x: number, y: number) =>
  frame().dispatchEvent(new PointerEvent(type, { clientX: x, clientY: y, pointerId: 1, bubbles: true }));
const marks = () => host.textContent ?? "";

it("keeps a Pixelate drag whose end point has not rendered yet when the button comes up", async () => {
  await act(async () => { pointer("pointerdown", 110, 10); });
  // The last move and the release arrive before React renders the move.
  await act(async () => {
    pointer("pointermove", 180, 20);
    pointer("pointerup", 180, 20);
  });
  expect(marks()).toContain("Pixelate 1");
});

it("keeps a Pixelate drag whose moves rendered before the button came up", async () => {
  await act(async () => { pointer("pointerdown", 110, 10); });
  await act(async () => { pointer("pointermove", 180, 20); });
  await act(async () => { pointer("pointerup", 180, 20); });
  expect(marks()).toContain("Pixelate 1");
});

it("records nothing for a drag shorter than the smallest mark", async () => {
  await act(async () => { pointer("pointerdown", 110, 10); });
  await act(async () => {
    pointer("pointermove", 112, 11);
    pointer("pointerup", 112, 11);
  });
  expect(marks()).not.toContain("Pixelate 1");
});

it("records nothing when the drag is cancelled", async () => {
  await act(async () => { pointer("pointerdown", 110, 10); });
  await act(async () => { pointer("pointermove", 180, 20); });
  await act(async () => { pointer("pointercancel", 180, 20); });
  await act(async () => { pointer("pointerup", 180, 20); });
  expect(marks()).not.toContain("Pixelate 1");
});

it("records nothing for a release that did not start on the photo", async () => {
  await act(async () => { pointer("pointermove", 110, 10); });
  await act(async () => { pointer("pointerup", 180, 20); });
  expect(marks()).not.toContain("Pixelate 1");
});
