import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceRuntimeProvider, type WorkspaceRuntime } from "../app/runtime";

const photos = [
  { id: "p1", url: "/api/conversations/c1/photos/p1/bytes?v=abc" },
  { id: "p2", url: "/api/conversations/c1/photos/p2/bytes?v=def" },
];
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: photos }),
  useMutation: () => ({ isPending: false, mutate: vi.fn() }),
  useQueryClient: () => ({}),
}));

const { ReviewPhotos } = await import("./RegenerateReviewDialog");
const { PhotoZoom } = await import("./BulkUploadDialog");

const hosted: WorkspaceRuntime = { hosted: true, features: new Set(), views: new Set(), marketplaceForms: new Set(), actionMarketplaces: new Set() };

describe("bulk Regenerate review photos", () => {
  // The web app's photos are private: a bare <img src="/api/..."> has no session and never loads.
  it("loads private thumbnails with the seller's session on the web", () => {
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={hosted}><ReviewPhotos convId="c1" onZoom={vi.fn()} /></WorkspaceRuntimeProvider>,
    );
    expect(html.match(/<img/g)).toHaveLength(2);
    expect(html).not.toContain('src="/api/');
  });

  it("loads the enlarged photo with the seller's session on the web", () => {
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={hosted}>
        <PhotoZoom urls={photos.map((photo) => photo.url)} index={0} rotating={false} onRotate={vi.fn()} onStep={vi.fn()} onClose={vi.fn()} />
      </WorkspaceRuntimeProvider>,
    );
    expect(html).toContain("photo-lightbox-image");
    expect(html).not.toContain('src="/api/');
  });

  it("keeps plain thumbnail addresses on the Mac", () => {
    const html = renderToStaticMarkup(<ReviewPhotos convId="c1" onZoom={vi.fn()} />);
    expect(html).toContain('src="/api/photos/p1/thumb?size=192&amp;v=abc"');
  });
});
