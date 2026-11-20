import { describe, expect, it } from "vitest";
import type { EtsyNotAllowedItem } from "../api/types";
import { etsyNotAllowedMeta, etsyNotAllowedSummary } from "./EtsyNotAllowed";

const item: EtsyNotAllowedItem = {
  conversation_id: "c1",
  title: "Delta Pro Weight L Collegiate Jayhawks T-Shirt",
  when_made: "2010 - 2019 (Recently)",
  who_made: "Another company or person",
  listed_at: "2026-10-01T00:00:00Z",
  etsy_url: "https://www.etsy.com/listing/1",
  title_says_vintage: false,
};
const now = Date.parse("2026-10-23T00:00:00Z");

describe("etsyNotAllowedMeta", () => {
  it("says what Etsy is told and how long it has been up", () => {
    expect(etsyNotAllowedMeta(item, now)).toBe("When made: 2010 - 2019 (Recently) · on Etsy 22 days");
  });

  it("points at When Made when the title dates the piece", () => {
    expect(etsyNotAllowedMeta({ ...item, when_made: "Made To Order (Not Yet Made)", title_says_vintage: true }, now))
      .toContain("Title says vintage: if it is 20+ years old, fix When Made instead");
  });

  it("copes with no answer and no date", () => {
    expect(etsyNotAllowedMeta({ ...item, when_made: "", listed_at: null }, now)).toBe("No When Made");
  });
});

describe("etsyNotAllowedSummary", () => {
  it("counts the listings", () => {
    expect(etsyNotAllowedSummary(1)).toBe("1 listing to delist on Etsy");
    expect(etsyNotAllowedSummary(256)).toBe("256 listings to delist on Etsy");
  });
});
