import { describe, expect, it } from "vitest";
import type { Photo } from "./api/types";
import { formPhotos, isVideoFile, videoRuleText, withPick } from "./marketplaceMedia";

const photo = (id: string, marketplace: string | null = null) => ({ id, marketplace, url: `/api/photos/${id}` }) as Photo;
const item = [photo("front"), photo("back"), photo("tag")];
const ebayOnly = [photo("stock", "ebay")];

describe("a marketplace form's photos", () => {
  it("uses the item's photos in item order until the seller changes them", () => {
    const form = formPhotos({}, item, ebayOnly, "ebay");
    expect(form.picked.map((p) => p.id)).toEqual(["front", "back", "tag"]);
    expect(form.unticked.map((p) => p.id)).toEqual(["stock"]);
    expect(form.custom).toBe(false);
  });

  it("sends the form's own pick in its order and lists the rest as left off", () => {
    const form = formPhotos({ marketplace_photos: { ebay: ["stock", "tag", "gone"] } }, item, ebayOnly, "ebay");
    expect(form.picked.map((p) => p.id)).toEqual(["stock", "tag"]);
    expect(form.unticked.map((p) => p.id)).toEqual(["front", "back"]);
    expect(form.custom).toBe(true);
  });

  it("stores no pick when the seller's order is the item's again, and keeps other forms' picks", () => {
    const listing = { title: "Coat", marketplace_photos: { ebay: ["back"], depop: ["tag"] } };
    expect(withPick(listing, "ebay", ["front", "back", "tag"], item)).toEqual({ title: "Coat", marketplace_photos: { depop: ["tag"] } });
    expect(withPick({ title: "Coat", marketplace_photos: { ebay: ["back"] } }, "ebay", [], item)).toEqual({ title: "Coat" });
    expect(withPick(listing, "ebay", ["tag", "front"], item).marketplace_photos).toEqual({ ebay: ["tag", "front"], depop: ["tag"] });
    expect(listing.marketplace_photos.ebay).toEqual(["back"]);
  });

  it("says each marketplace's video rules in one line and tells videos from photos", () => {
    expect(videoRuleText({ name: "eBay", max_bytes: 157286400, size_label: "150 MB", mime_types: ["video/mp4", "video/quicktime"] }))
      .toBe("One video: MP4 or MOV, up to 150 MB.");
    expect(videoRuleText({ name: "Poshmark", shorter_than: 15 })).toBe("One video: shorter than 15 seconds.");
    expect(videoRuleText({ name: "Etsy", min_seconds: 3, max_seconds: 15, size_label: "95 MB" })).toBe("One video: 3 to 15 seconds, up to 95 MB.");
    expect(isVideoFile({ name: "clip.mov", type: "video/quicktime" })).toBe(true);
    expect(isVideoFile({ name: "IMG_1.HEIC", type: "" })).toBe(false);
    expect(isVideoFile({ name: "clip.MP4", type: "" })).toBe(true);
  });
});
