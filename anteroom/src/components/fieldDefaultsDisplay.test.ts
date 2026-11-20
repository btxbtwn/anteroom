import { describe, expect, it } from "vitest";
import { shownSavedDefault } from "./fieldDefaults";

const saved = { value: "2551", label: "USPS Ground Advantage" };

describe("what a row shows for a saved default", () => {
  it("shows the saved carrier when it is one of this package's labels", () => {
    expect(shownSavedDefault({ selectionOnly: true, options: ["USPS Ground Advantage", "UPS Ground Saver"], defaultValue: "" }, saved))
      .toBe("USPS Ground Advantage");
  });

  it("keeps the form's own value: the saved carrier already matched to this weight tier", () => {
    expect(shownSavedDefault({ selectionOnly: true, options: ["USPS Ground Advantage"], defaultValue: "2553" }, saved))
      .toBeUndefined();
  });

  it("does not show a saved carrier this package's labels do not offer, or before they load", () => {
    expect(shownSavedDefault({ selectionOnly: true, options: ["UPS Ground Saver"], defaultValue: "" }, saved)).toBeUndefined();
    expect(shownSavedDefault({ selectionOnly: true, options: [], defaultValue: "" }, saved)).toBeUndefined();
    expect(shownSavedDefault({ selectionOnly: true, defaultValue: undefined }, saved)).toBeUndefined();
  });

  it("shows a typed default such as a ZIP, and nothing when none is saved", () => {
    expect(shownSavedDefault({ defaultValue: "" }, { value: "70115", label: "70115" })).toBe("70115");
    expect(shownSavedDefault({ defaultValue: "" }, undefined)).toBeUndefined();
  });
});
