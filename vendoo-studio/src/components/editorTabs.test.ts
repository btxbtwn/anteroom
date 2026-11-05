import { describe, expect, it } from "vitest";
import { editorTabs } from "./editorTabs";

const mac = { hosted: false, marketplaceForms: new Set<string>() };

describe("editorTabs", () => {
  it("leaves out a marketplace the seller turned off", () => {
    expect(editorTabs(["ebay", "poshmark", "mercari", "depop", "etsy"], mac))
      .toEqual(["general", "ebay", "poshmark", "mercari", "depop", "etsy"]);
  });

  it("keeps only selected marketplaces that have forms", () => {
    expect(editorTabs(["ebay"], mac)).toEqual(["general", "ebay"]);
  });

  it("on the web shows only forms the hosted app has", () => {
    expect(editorTabs(["ebay", "poshmark"], { hosted: true, marketplaceForms: new Set(["ebay"]) })).toEqual(["general", "ebay"]);
  });
});
