import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { PhotoLightbox } from "./PhotoThumbs";

// Dialogs render into a portal, which static markup leaves empty.
vi.mock("./ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogPopup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

it("offers direct rotation and only offers revert when there are saved edits", () => {
  const props = { urls: ["/a.jpg"], index: 0, onRotate: vi.fn(), onEdit: vi.fn(), onStudio: vi.fn(), onStep: vi.fn(), onClose: vi.fn() };
  const original = renderToStaticMarkup(<PhotoLightbox {...props} />);
  const edited = renderToStaticMarkup(<PhotoLightbox {...props} onRevert={vi.fn()} />);
  expect(original).toContain('aria-label="Rotate left"');
  expect(original).toContain('aria-label="Rotate right"');
  expect(original).toContain('aria-label="Edit photo"');
  expect(original).toContain('aria-label="AI Studio"');
  expect(original).not.toContain("Revert to original");
  expect(edited).toContain("Revert to original");
  expect(edited).not.toContain("Crop, adjust or remove the background");
});
