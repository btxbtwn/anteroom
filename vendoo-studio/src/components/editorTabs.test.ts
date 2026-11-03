import { describe, expect, it } from "vitest";
import { editorTabs } from "./editorTabs";

const mac = { hosted: false, marketplaceForms: new Set<string>() };

describe("editorTabs", () => {
  it("leaves out a marketplace the seller turned off, Vinted included", () => {
    expect(editorTabs(["ebay", "poshmark", "mercari", "depop", "etsy", "sellwild"], mac))
      .toEqual(["general", "ebay", "poshmark", "mercari", "depop", "etsy", "sellwild"]);
  });

  it("shows Vinted when the seller lists there", () => {
    expect(editorTabs(["ebay", "vinted"], mac)).toEqual(["general", "ebay", "vinted"]);
  });

  it("on the web shows only forms the hosted app has", () => {
    expect(editorTabs(["ebay", "poshmark"], { hosted: true, marketplaceForms: new Set(["ebay"]) })).toEqual(["general", "ebay"]);
  });
});
