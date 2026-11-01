import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { Photo } from "../api/types";
import { WorkspaceRuntimeProvider } from "../app/runtime";
import { PhotoEditor } from "./PhotoEditor";
import { PhotoLightbox } from "./PhotoThumbs";

// Dialogs render into a portal, which static markup leaves empty.
vi.mock("./ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogPopup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

const photo: Photo = {
  id: "p1", conversation_id: "item", original_filename: "front.jpg", mime_type: "image/jpeg", size_bytes: 10,
  display_order: 0, width: 400, height: 300, created_at: "", url: "/api/photos/p1?v=abc",
  edits: { rotate: 0, straighten: 0, crop: null, exposure: 0.5, brightness: 0, contrast: 0, background: "#ffffff" },
};

it("opens on the crop tools with marketplace shapes and a reset to the original", () => {
  const html = renderToStaticMarkup(<PhotoEditor convId="item" photo={photo} index={1} onSaved={vi.fn()} onClose={vi.fn()} />);
  expect(html).toContain("Edit photo 2");
  for (const label of ["Free", "Square", "4:5", "3:4", "4:3", "Straighten", "Reset to original", "Save"]) {
    expect(html).toContain(label);
  }
  expect(html).toContain("The original is kept");
  expect(html).toContain(">Background<");
  // Nothing has changed yet, so there is nothing to save.
  expect(html).toMatch(/disabled=""[^>]*>Save/);
});

it("offers Edit from the enlarged photo and says when it is already edited", () => {
  const html = renderToStaticMarkup(
    <PhotoLightbox urls={["/a.jpg"]} index={0} onRotate={vi.fn()} onEdit={vi.fn()} edited onStep={vi.fn()} onClose={vi.fn()} />,
  );
  expect(html).toContain("Edited");
  const plain = renderToStaticMarkup(<PhotoLightbox urls={["/a.jpg"]} index={0} onRotate={vi.fn()} onStep={vi.fn()} onClose={vi.fn()} />);
  expect(plain).not.toContain("Crop, adjust or remove the background");
});

it("keeps shared photo edits available without offering an unconnected background worker", () => {
  const html = renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(["photo_edits"]), views: new Set(), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
      <PhotoEditor convId="item" photo={{ ...photo, edits: undefined }} index={0} onSaved={vi.fn()} onClose={vi.fn()} />
    </WorkspaceRuntimeProvider>,
  );
  expect(html).toContain("Crop");
  expect(html).toContain("Adjust");
  expect(html).toContain("Reset to original");
  expect(html).not.toContain(">Background<");
  expect(html).not.toContain('value="background"');
});
