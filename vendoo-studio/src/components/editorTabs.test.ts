import { describe, expect, it } from "vitest";
import { editorTabs } from "./editorTabs";

const mac = { cloud: false, marketplaceForms: new Set(["ebay", "poshmark", "mercari", "depop", "etsy"]) };

describe("editorTabs", () => {
  it("leaves out a marketplace the seller turned off", () => {
    expect(editorTabs(["ebay", "poshmark", "mercari", "depop", "etsy"], mac))
      .toEqual(["general", "ebay", "poshmark", "mercari", "depop", "etsy"]);
  });

  it("keeps only selected marketplaces that have forms", () => {
    expect(editorTabs(["ebay"], mac)).toEqual(["general", "ebay"]);
  });
});
