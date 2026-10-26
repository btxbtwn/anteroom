import { describe, expect, it } from "vitest";
import { bulkEditsFromInputs, bulkReviewTokens, type BulkReviewRow } from "./bulkEdit";

describe("bulk edit review", () => {
  it("distinguishes unchecked fields, explicit clears and confirmed zero costs", () => {
    expect(bulkEditsFromInputs({ description: "", cost: "0", price: "45" }, "add", " Winter, Fall, Winter ")).toEqual({
      values: { description: "", cost: 0, price: 45 }, labels_mode: "add", labels: ["Winter", "Fall"],
    });
    expect(bulkEditsFromInputs({ cost: "" }, "keep", "").values.cost).toBeNull();
    expect(bulkEditsFromInputs({}, "replace", "")).toEqual({ values: {}, labels_mode: "replace", labels: [] });
  });
  it("rejects invalid prices, quantities, missing required text and empty requests", () => {
    for (const fields of [{price:"0"}, {price:"1.5"}, {quantity:"0"}, {quantity:"NaN"}, {size:" "}, {title:""}, {}]) {
      expect(() => bulkEditsFromInputs(fields, "keep", "")).toThrow();
    }
    expect(() => bulkEditsFromInputs({}, "add", "")).toThrow();
  });
  it("submits only reviewed actionable rows with their concurrency tokens", () => {
    const row: BulkReviewRow = { conversation_id: "coat", expected_revision_id: "rev", expected_basis: "a".repeat(64), expected_result: "b".repeat(64), title: "Coat", skip_reason: null, changes: [{field:"sku",old:"old",new:"new"}] };
    expect(bulkReviewTokens([row, {...row,conversation_id:"busy",skip_reason:"Busy"}, {...row,conversation_id:"same",changes:[]}])).toEqual([
      {conversation_id:"coat",expected_revision_id:"rev",expected_basis:row.expected_basis,expected_result:row.expected_result},
    ]);
  });
});
