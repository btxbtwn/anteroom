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

it("inside the marketplace's own form, shows its hint and slot captions instead of Anteroom's heading", () => {
  serve([photo("front"), photo("back")]);
  const slots = ["Cover photo", "Front", "Back", "Side", "Label", "Detail", "Flaw"];
  const html = renderToStaticMarkup(<MarketplaceMedia convId="c1" marketplace="depop" listing={{}} onChange={vi.fn()}
    page={{ hint: "Add up to 8 photos in JPEG or PNG format.", slots, accept: "image/jpeg,image/png,.jpg,.jpeg,.png" }} />);
  expect(html).toContain("Add up to 8 photos in JPEG or PNG format.");
  expect(html).toContain('accept="image/jpeg,image/png,.jpg,.jpeg,.png"');
  expect(html).not.toContain("sent to Depop");
  expect(html).not.toContain("Depop takes up to 8 photos.");
  // Every slot keeps the page's caption, filled or empty, in the page's order.
  const positions = slots.map((caption) => html.indexOf(`>${caption}</span>`));
  expect(positions.every((at) => at >= 0)).toBe(true);
  expect(positions).toEqual([...positions].sort((a, b) => a - b));
  expect(html.indexOf("/api/photos/front")).toBeLessThan(html.indexOf(">Cover photo</span>"));
});

it("inside the form, keeps a video on a marketplace whose page has one", () => {
  serve([photo("front")]);
  const html = renderToStaticMarkup(<MarketplaceMedia convId="c1" marketplace="poshmark" listing={{ poshmark_specifics: { videoId: "short" } }}
    onChange={vi.fn()} page={{ hint: "Add up to 16 photos and 1 video to show the angles and details of your item." }} />);
  expect(html).toContain("Add up to 16 photos and 1 video to show the angles and details of your item.");
  expect(html).toContain('aria-label="Poshmark video"');
  expect(html).toContain('accept="image/*,video/*"');
  expect(html).not.toContain("sent to Poshmark");
});

it("inside the form, still lets the seller send a different pick and warns past the limit", () => {
  serve(Array.from({ length: 13 }, (_, index) => photo(`p${index + 1}`)));
  const html = renderToStaticMarkup(<MarketplaceMedia convId="c1" marketplace="mercari"
    listing={{ marketplace_photos: { mercari: Array.from({ length: 13 }, (_, index) => `p${13 - index}`) } }}
    onChange={vi.fn()} page={{ hint: "Add up to 12 photos\nOr drag and drop" }} />);
  expect(html).toContain("Add up to 12 photos");
  expect(html).toContain("Or drag and drop");
  expect(html).toContain('accept="image/*"');
  expect(html).toContain("Reset to item photos");
  expect(html).toContain("Mercari keeps 12 photos. The first 12 of 13 are sent.");
});
