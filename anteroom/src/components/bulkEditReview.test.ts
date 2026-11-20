import { expect, it } from "vitest";
import type { BulkReviewRow } from "../api/bulkEdit";
import { bulkEditChanges } from "./bulkEditReview";

it("reviews nested marketplace changes individually and omits internal metadata", () => {
  const row: BulkReviewRow = {
    conversation_id: "fixture", title: "Fixture shirt", skip_reason: null,
    expected_revision_id: "revision", expected_basis: "basis", expected_result: "result",
    changes: [
      { field: "brand", old: "Old", new: "New" },
      { field: "poshmark_specifics", old: { brand: "Old", description: "Keep this" }, new: { description: "Keep this" } },
      { field: "_seller_fields", old: ["title"], new: ["brand", "title"] },
    ],
  };
  expect(bulkEditChanges(row)).toEqual([
    { field: "brand", before: "Old", after: "New" },
    { field: "poshmark_specifics.brand", before: "Old", after: undefined },
  ]);
});
