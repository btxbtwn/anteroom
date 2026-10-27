import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";

const { useQuery } = vi.hoisted(() => ({ useQuery: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery, useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));

const { MarketplaceMedia } = await import("./MarketplaceMedia");

const photo = (id: string, marketplace: string | null = null) => ({ id, marketplace, url: `/api/photos/${id}`, original_filename: `${id}.jpg` });
const RULES = {
  photo_limits: { ebay: 24, depop: 8, mercari: 12, poshmark: 16, etsy: 20, vinted: 20, sellwild: 20 },
  video_rules: { poshmark: { name: "Poshmark", shorter_than: 15 }, ebay: { name: "eBay", size_label: "150 MB", mime_types: ["video/mp4", "video/quicktime"] } },
};
const VIDEOS = { videos: [
  { id: "short", original_filename: "short.mp4", duration_seconds: 6, size_bytes: 10, mime_type: "video/mp4", url: "/api/videos/short" },
  { id: "long", original_filename: "long.mp4", duration_seconds: 20, size_bytes: 10, mime_type: "video/mp4", url: "/api/videos/long" },
] };

function serve(item: object[], own: object[] = []) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: unknown[] }) => ({
    data: queryKey[0] === "marketplace-media" ? RULES
      : queryKey[0] === "listing-videos" ? VIDEOS
        : queryKey.length === 3 ? own : item,
  }));
}

beforeEach(() => {
  useQuery.mockReset();
});

it("shows the photos the marketplace is sent, in order, with its limit, and marks the ones past it", () => {
  serve(Array.from({ length: 9 }, (_, index) => photo(`p${index + 1}`)));
  const html = renderToStaticMarkup(<MarketplaceMedia convId="c1" marketplace="depop" listing={{}} onChange={vi.fn()} />);
  expect(html).toContain("8 of 8 sent to Depop · the item&#x27;s photos");
  expect(html).toContain("Depop takes up to 8 photos.");
  expect(html.match(/Not sent/g)).toHaveLength(1);
  expect(html).not.toContain("Reset to item photos");
  // Depop takes no video from its website, so there is nothing to add one with.
  expect(html).not.toContain("video");
});

it("puts the video in the same strip on a marketplace that takes one, with its rules inline", () => {
  serve([photo("front")], [photo("model", "poshmark")]);
  const listing = { marketplace_photos: { poshmark: ["model", "front"] }, poshmark_specifics: { videoId: "short" } };
  const html = renderToStaticMarkup(<MarketplaceMedia convId="c1" marketplace="poshmark" listing={listing} onChange={vi.fn()} />);
  expect(html).toContain("Add photos or video");
  expect(html).toContain('aria-label="Poshmark video"');
  expect(html).toContain("One video: shorter than 15 seconds.");
  expect(html).toContain("Reset to item photos");
  expect(html).toContain("2 of 16 sent to Poshmark · this form&#x27;s own");
  expect(html.indexOf("/api/photos/model")).toBeLessThan(html.indexOf("/api/photos/front"));
  // The long video stays in the library but cannot be used on Poshmark.
  expect(html).toContain("Not accepted");
  expect(html).not.toContain('aria-label="Use long.mp4"');
});
