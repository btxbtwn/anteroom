import { describe, expect, it } from "vitest";
import type { MarkdownPreviewRow } from "./api/types";
import {
  applyMarkdownsLabel,
  canApplyMarkdowns,
  draftError,
  draftsFromPreview,
  dropPercentLabel,
  markdownDrops,
  markdownOutcomes,
  markdownSummary,
  staleSuggestionIds,
  type MarkdownDraft,
} from "./bulkPriceDrop";

function row(overrides: Partial<MarkdownPreviewRow> = {}): MarkdownPreviewRow {
  return {
    conversation_id: "a",
    title: "Nike tee",
    cover_photo_url: null,
    revision_id: "rev-a",
    current_price: 48,
    suggested_price: 43,
    suggested_effective_percent: 10.4,
    suggested_mode: "percent",
    suggested_reason: "Standard markdown.",
    age_days: 90,
    skip_reason: null,
    ...overrides,
  };
}

function draft(overrides: Partial<MarkdownDraft> = {}): MarkdownDraft {
  return { ...draftsFromPreview([row()])[0], ...overrides };
}

describe("draftsFromPreview", () => {
  it("starts each row on its suggestion and skips the ones that cannot drop", () => {
    const drafts = draftsFromPreview([
      row(),
      row({ conversation_id: "b", current_price: null, suggested_price: null, skip_reason: "Listing has no price to drop" }),
    ]);
    expect(drafts[0]).toMatchObject({ id: "a", priceText: "43", skip: false, unavailable: null, revisionId: "rev-a" });
    expect(drafts[1]).toMatchObject({ id: "b", priceText: "", skip: true, unavailable: "Listing has no price to drop" });
  });
});

describe("draftError", () => {
  it("accepts a lower whole-dollar price", () => {
    expect(draftError(draft({ priceText: "40" }))).toBeNull();
  });

  it("rejects empty, fractional, and prices that would not fall", () => {
    expect(draftError(draft({ priceText: " " }))).toBe("Enter a price or skip");
    expect(draftError(draft({ priceText: "40.5" }))).toBe("Whole dollars only");
    expect(draftError(draft({ priceText: "0" }))).toBe("Whole dollars only");
    expect(draftError(draft({ priceText: "48" }))).toBe("Must be lower than now");
    expect(draftError(draft({ priceText: "60" }))).toBe("Must be lower than now");
  });

  it("ignores skipped rows", () => {
    expect(draftError(draft({ priceText: "60", skip: true }))).toBeNull();
  });
});

describe("markdownDrops", () => {
  it("sends only rows not skipped, with the revision the seller reviewed", () => {
    const drafts = [
      draft({ priceText: "40" }),
      draft({ id: "b", revisionId: "rev-b", priceText: "30", skip: true }),
      draft({ id: "c", revisionId: null, unavailable: "Busy", skip: false, priceText: "10" }),
    ];
    expect(markdownDrops(drafts)).toEqual([
      { conversation_id: "a", price: 40, expected_revision_id: "rev-a" },
    ]);
  });

  it("cannot apply while a row has a bad price or nothing would change", () => {
    expect(canApplyMarkdowns([draft(), draft({ id: "b", priceText: "99" })])).toBe(false);
    expect(canApplyMarkdowns([draft({ skip: true })])).toBe(false);
    expect(canApplyMarkdowns([draft(), draft({ id: "b", priceText: "99", skip: true })])).toBe(true);
  });
});

describe("labels", () => {
  it("states the cut the typed price makes", () => {
    expect(dropPercentLabel(48, "43")).toBe("−10.4%");
    expect(dropPercentLabel(48, "48")).toBe("");
    expect(dropPercentLabel(null, "10")).toBe("");
  });

  it("counts the markdowns on the apply button", () => {
    expect(applyMarkdownsLabel(0)).toBe("Apply markdowns");
    expect(applyMarkdownsLabel(1)).toBe("Apply 1 markdown");
    expect(applyMarkdownsLabel(3)).toBe("Apply 3 markdowns");
  });
});

describe("markdownOutcomes", () => {
  it("reports saved and refused rows in review order", () => {
    const drafts = [draft({ id: "b", title: "Coat" }), draft(), draft({ id: "c", skip: true })];
    const outcomes = markdownOutcomes(drafts, {
      applied: [{ conversation_id: "a", revision_id: "r", price: 40, previous_price: 48, valid: true }],
      skipped: [{ conversation_id: "b", reason: "This listing changed since you reviewed it" }],
    });
    expect(outcomes).toEqual([
      { id: "b", title: "Coat", from: null, to: null, reason: "This listing changed since you reviewed it" },
      { id: "a", title: "Nike tee", from: 48, to: 40, reason: null },
    ]);
    expect(markdownSummary(outcomes)).toBe("1 price dropped in Anteroom, 1 not changed");
    expect(markdownSummary([])).toBe("No prices changed");
  });
});

describe("staleSuggestionIds", () => {
  it("picks the stale listings from the feed", () => {
    expect(staleSuggestionIds([
      { conversation_id: "a", kind: "stale_active" },
      { conversation_id: "b", kind: "failed" },
      { conversation_id: "c", kind: "stale_active" },
    ])).toEqual(["a", "c"]);
  });
});
