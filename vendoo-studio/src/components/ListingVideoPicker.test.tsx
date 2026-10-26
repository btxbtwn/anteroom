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
  renderToStaticMarkup(<ListingVideoPicker convId="item" selectedId="" onSelect={onSelect} />);
  useMutation.mock.calls[0][0].onSuccess();
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["listing-videos", "item"] });
  expect(onSelect).not.toHaveBeenCalled();
});

it("shows the local preview, blocks a 15-second selection and protects the selected file", () => {
  useQuery.mockReturnValue({ data: { videos: [
    { id: "selected", original_filename: "short.mp4", duration_seconds: 3, url: "/api/videos/selected" },
    { id: "long", original_filename: "long.mp4", duration_seconds: 15, url: "/api/videos/long" },
  ] } });
  const html = renderToStaticMarkup(<ListingVideoPicker convId="item" selectedId="selected" onSelect={vi.fn()} />);
  expect(html).toContain('src="/api/videos/selected"');
  expect(html).toContain("Clear video");
  expect(html).toMatch(/disabled=""[^>]*>Delete video/);
  expect(html).toMatch(/disabled=""[^>]*>Use this video/);
  expect(html).toContain("shorter than 15 seconds");
});

it("makes a missing selection and an upload refusal visible", () => {
  useMutation.mockReturnValue({ isPending: false, error: new Error("Video could not be read") });
  const html = renderToStaticMarkup(<ListingVideoPicker convId="item" selectedId="missing" onSelect={vi.fn()} />);
  expect(html).toContain("The selected video is missing");
  expect(html).toContain("Video could not be read");
});
