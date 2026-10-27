import { describe, expect, it } from "vitest";
import { initialOpenListing, openListingReducer } from "./openListing";

describe("open listing", () => {
  it("opens a deep-linked listing immediately", () => {
    expect(initialOpenListing("linked")).toEqual(openListingReducer(null, { type: "open", id: "linked" }));
  });
  it("keeps state when reopening the same listing", () => {
    let state = initialOpenListing("a");
    state = openListingReducer(state, { type: "review", id: "a", value: "forms" });
    expect(openListingReducer(state, { type: "open", id: "a" })).toBe(state);
  });
  it("replaces the open listing with a fresh one", () => {
    let state = initialOpenListing("a");
    state = openListingReducer(state, { type: "message", id: "a", value: "Fix price" });
    state = openListingReducer(state, { type: "open", id: "b" });
    expect(state).toEqual({ id: "b", reviewTab: "input", nonce: 0, queuedMessage: null });
  });
  it("ignores actions for a listing that is no longer open", () => {
    const state = openListingReducer(initialOpenListing("a"), { type: "open", id: "b" });
    for (const action of [
      { type: "close", id: "a" },
      { type: "clear", id: "a" },
      { type: "message", id: "a", value: "x" },
    ] as const) expect(openListingReducer(state, action)).toBe(state);
  });
  it("closes and clears the open listing", () => {
    let state = initialOpenListing("a");
    state = openListingReducer(state, { type: "review", id: "a", value: "forms" });
    expect(openListingReducer(state, { type: "clear", id: "a" })).toMatchObject({ nonce: 1, reviewTab: "input" });
    expect(openListingReducer(state, { type: "close", id: "a" })).toBeNull();
  });
});
