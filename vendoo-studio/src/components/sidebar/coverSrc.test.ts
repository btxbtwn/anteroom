import { describe, expect, it } from "vitest";
import { displayCoverUrl } from "./coverSrc";

describe("displayCoverUrl", () => {
  it("leaves a local thumbnail alone", () => {
    expect(displayCoverUrl("/api/photos/abc/thumb?size=96")).toBe("/api/photos/abc/thumb?size=96");
  });

  it("sends an https cover through the thumbnail", () => {
    expect(displayCoverUrl("https://images.vendoo.co/abc")).toBe(
      "/api/cover-thumb?url=https%3A%2F%2Fimages.vendoo.co%2Fabc",
    );
  });

  it("drops addresses that are not a public image URL", () => {
    expect(displayCoverUrl(null)).toBeNull();
    expect(displayCoverUrl(undefined)).toBeNull();
    expect(displayCoverUrl("")).toBeNull();
    expect(displayCoverUrl("http://images.vendoo.co/abc")).toBeNull();
    expect(displayCoverUrl("//images.vendoo.co/abc")).toBeNull();
    expect(displayCoverUrl("javascript:alert(1)")).toBeNull();
    expect(displayCoverUrl("data:image/png;base64,aaaa")).toBeNull();
  });
});
