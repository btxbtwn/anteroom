import { expect, it } from "vitest";
import { agentFieldLocked } from "./agentFieldLocks";

it("locks only the target marketplace field including its native spelling", () => {
  const locks = ["ebay_specifics.Size Type"];
  expect(agentFieldLocked("ebay_specifics.sizeType", locks)).toBe(true);
  expect(agentFieldLocked("ebay_specifics.Size", locks)).toBe(false);
  expect(agentFieldLocked("mercari_specifics.sizeType", locks)).toBe(false);
  expect(agentFieldLocked("price", locks)).toBe(false);
});

it("locks nested controls and releases them when work ends", () => {
  expect(agentFieldLocked("depop_specifics.attributes.fit", ["depop_specifics.attributes"])).toBe(true);
  expect(agentFieldLocked("depop_specifics.attributes", ["depop_specifics.attributes.fit"])).toBe(true);
  expect(agentFieldLocked("depop_specifics.attributes.fit", [])).toBe(false);
});
