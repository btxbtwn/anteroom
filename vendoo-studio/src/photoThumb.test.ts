import { describe, expect, it } from "vitest";
import { photoThumbUrl, STRIP_THUMB_SIZE } from "./photoThumb";

describe("photoThumbUrl", () => {
  it("keeps the photo version so a rotate is not a stale thumbnail", () => {
    expect(photoThumbUrl({ id: "photo 1", url: "/api/photos/photo%201?v=abc123" })).toBe(
      `/api/photos/photo%201/thumb?size=${STRIP_THUMB_SIZE}&v=abc123`,
    );
  });

  it("still names the thumbnail when the photo URL has no version", () => {
    expect(photoThumbUrl({ id: "abc", url: "/api/photos/abc" })).toBe(
      `/api/photos/abc/thumb?size=${STRIP_THUMB_SIZE}`,
    );
  });

  it("reads the version from an absolute photo URL", () => {
    expect(photoThumbUrl({ id: "abc", url: "http://127.0.0.1:8765/api/photos/abc?v=ff" })).toBe(
      `/api/photos/abc/thumb?size=${STRIP_THUMB_SIZE}&v=ff`,
    );
  });
});
