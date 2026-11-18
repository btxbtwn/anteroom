import { describe, expect, it } from "vitest";
import { savedDefaultFor } from "./fieldDefaults";

const defaults = {
  "mercari_specifics.shipsFrom": { value: "70125", label: "70125" },
  "mercari_specifics.shippingClassId": { value: "2550", label: "USPS Ground Advantage" },
  "mercari_specifics.package.fitsShoebox": { value: "no", label: "No" },
};

describe("saved listing field defaults", () => {
  it("matches the value the seller typed", () => {
    expect(savedDefaultFor(defaults, "mercari_specifics.shipsFrom", "70125", "70125"))
      .toEqual({ value: "70125", label: "70125" });
    expect(savedDefaultFor(defaults, "mercari_specifics.shipsFrom", "90210", "90210")).toBeNull();
  });

  it("matches a shipping label by its name even when this weight tier has another id", () => {
    expect(savedDefaultFor(defaults, "mercari_specifics.shippingClassId", "2600", "USPS Ground Advantage"))
      .toEqual({ value: "2550", label: "USPS Ground Advantage" });
  });

  it("has no default for a field the seller never saved", () => {
    expect(savedDefaultFor(defaults, "mercari_specifics.shippingMethod", "MERCARI", "Prepaid label")).toBeNull();
    expect(savedDefaultFor(undefined, "mercari_specifics.shipsFrom", "70125", "70125")).toBeNull();
  });

  it("matches the shoebox answer by its stored value or its label, and No is an answer", () => {
    const path = "mercari_specifics.package.fitsShoebox";
    expect(savedDefaultFor(defaults, path, "no", "No")).toEqual({ value: "no", label: "No" });
    expect(savedDefaultFor(defaults, path, "", "No")).toEqual({ value: "no", label: "No" });
    expect(savedDefaultFor(defaults, path, "yes", "Yes")).toBeNull();
    expect(savedDefaultFor({ [path]: { value: "yes", label: "Yes" } }, path, "yes", "Yes"))
      .toEqual({ value: "yes", label: "Yes" });
    expect(savedDefaultFor({ [path]: { value: "yes", label: "Yes" } }, path, "no", "No")).toBeNull();
  });
});
