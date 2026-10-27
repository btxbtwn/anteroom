import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, expect, it, vi } from "vitest";
import { ListingVideoPicker } from "./ListingVideoPicker";

const { useQuery, useMutation, invalidateQueries } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn(), invalidateQueries: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery, useMutation, useQueryClient: () => ({ invalidateQueries }) }));
beforeEach(() => {
  useQuery.mockReset(); useMutation.mockReset(); invalidateQueries.mockReset();
  useQuery.mockReturnValue({ data: { videos: [] }, isLoading: false });
  useMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
});

it("uploads into the library without selecting a video or changing the listing", () => {
  const onSelect = vi.fn();
  renderToStaticMarkup(<ListingVideoPicker convId="item" marketplace="poshmark" selectedId="" onSelect={onSelect} />);
  useMutation.mock.calls[0][0].onSuccess();
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["listing-videos", "item"] });
  expect(onSelect).not.toHaveBeenCalled();
});

it("shows the local preview, blocks a 15-second selection and protects the selected file", () => {
  useQuery.mockReturnValue({ data: { rules: { poshmark: { name: "Poshmark", shorter_than: 15 } }, videos: [
    { id: "selected", original_filename: "short.mp4", duration_seconds: 3, url: "/api/videos/selected" },
    { id: "long", original_filename: "long.mp4", duration_seconds: 15, url: "/api/videos/long" },
  ] } });
  const html = renderToStaticMarkup(<ListingVideoPicker convId="item" marketplace="poshmark" selectedId="selected" onSelect={vi.fn()} />);
  expect(html).toContain('src="/api/videos/selected"');
  expect(html).toContain("Clear video");
  expect(html).toMatch(/disabled=""[^>]*>Delete video/);
  expect(html).toMatch(/disabled=""[^>]*>Use this video/);
  expect(html).toContain("shorter than 15 seconds");
});

it("makes a missing selection and an upload refusal visible", () => {
  useMutation.mockReturnValue({ isPending: false, error: new Error("Video could not be read") });
  const html = renderToStaticMarkup(<ListingVideoPicker convId="item" marketplace="poshmark" selectedId="missing" onSelect={vi.fn()} />);
  expect(html).toContain("The selected video is missing");
  expect(html).toContain("Video could not be read");
});

it("applies the selected marketplace's own limits", () => {
  const rules = {
    etsy: { name: "Etsy", min_seconds: 3, max_seconds: 15, max_bytes: 95_000_000, size_label: "95 MB", mime_types: ["video/mp4"] },
    ebay: { name: "eBay", max_bytes: 157_286_400, size_label: "150 MB", mime_types: ["video/mp4", "video/quicktime"] },
  };
  useQuery.mockReturnValue({ data: { rules, videos: [
    { id: "short", original_filename: "short.mp4", duration_seconds: 2, size_bytes: 10, mime_type: "video/mp4", url: "/a" },
    { id: "long", original_filename: "long.mov", duration_seconds: 40, size_bytes: 10, mime_type: "video/quicktime", url: "/b" },
  ] } });
  const etsy = renderToStaticMarkup(<ListingVideoPicker convId="item" marketplace="etsy" selectedId="" onSelect={vi.fn()} />);
  expect(etsy).toContain("from 3 to 15 seconds long for Etsy");
  expect(etsy.match(/disabled=""[^>]*>Use this video/g)).toHaveLength(2);
  const ebay = renderToStaticMarkup(<ListingVideoPicker convId="item" marketplace="ebay" selectedId="" onSelect={vi.fn()} />);
  expect(ebay).not.toMatch(/disabled=""[^>]*>Use this video/);
});
