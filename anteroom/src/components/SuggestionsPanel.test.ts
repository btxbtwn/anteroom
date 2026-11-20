import { describe, expect, it } from "vitest";
import {
  SUGGESTION_KIND_LABELS,
  markDownStaleLabel,
  staleAgeLabel,
  suggestionHint,
  suggestionKindLabel,
} from "./SuggestionsPanel";

describe("suggestionKindLabel", () => {
  it("names the next step for every ranked kind", () => {
    expect(suggestionKindLabel("failed")).toBe("Fix failed listing");
    expect(suggestionKindLabel("ready_to_generate")).toBe("Generate listing");
    expect(suggestionKindLabel("fix_validation")).toBe("Fix listing fields");
    expect(suggestionKindLabel("stale_active")).toBe("Refresh or consider price drop");
    expect(suggestionKindLabel("ready_to_review")).toBe("Review listing");
  });

  it("covers every kind the API can return", () => {
    expect(Object.keys(SUGGESTION_KIND_LABELS).sort()).toEqual([
      "failed",
      "fix_validation",
      "ready_to_generate",
      "ready_to_review",
      "stale_active",
    ]);
  });
});

describe("suggestionHint", () => {
  it("states each kind on the listing's own row", () => {
    expect(suggestionHint({ kind: "ready_to_generate", age_days: null })).toBe("Ready to generate");
    expect(suggestionHint({ kind: "ready_to_review", age_days: null })).toBe("Needs review");
    expect(suggestionHint({ kind: "fix_validation", age_days: null })).toBe("Fields to fix");
    expect(suggestionHint({ kind: "failed", age_days: null })).toBe("Last send failed");
    expect(suggestionHint({ kind: "stale_active", age_days: null })).toBe("Stale");
  });

  it("puts a stale row's listed age first, then why it ranks high", () => {
    expect(staleAgeLabel(1)).toBe("1 day");
    expect(staleAgeLabel(47)).toBe("47 days");
    expect(suggestionHint({ kind: "stale_active", age_days: 47 })).toBe("Stale 47 days");
    expect(suggestionHint({ kind: "stale_active", age_days: 75, similar_sold: 3, season_fit: "in" }))
      .toBe("Stale 75 days · 3 like it sold · In season");
  });
});

describe("markDownStaleLabel", () => {
  it("counts the stale listings the markdown covers", () => {
    expect(markDownStaleLabel(1)).toBe("Mark down 1 stale listing");
    expect(markDownStaleLabel(4)).toBe("Mark down 4 stale listings");
  });
});
