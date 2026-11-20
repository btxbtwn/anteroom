import { describe, expect, it } from "vitest";
import { initialOpenListing, isBrowserLinkClick, listingHref, openListingReducer, phonePaneOnOpen } from "./openListing";

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
    expect(state).toEqual({ id: "b", reviewTab: "input", editTab: "general", nonce: 0, queuedMessage: null });
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
    state = openListingReducer(state, { type: "tab", id: "a", value: "depop" });
    expect(openListingReducer(state, { type: "clear", id: "a" })).toMatchObject({ nonce: 1, reviewTab: "input", editTab: "general" });
    expect(openListingReducer(state, { type: "close", id: "a" })).toBeNull();
  });
  it("opens a listing on a marketplace's tab", () => {
    expect(openListingReducer(null, { type: "open", id: "a", tab: "poshmark" }))
      .toMatchObject({ id: "a", reviewTab: "forms", editTab: "poshmark" });
  });
  it("moves an open listing to the tab asked for, keeping its chat", () => {
    let state = initialOpenListing("a");
    state = openListingReducer(state, { type: "message", id: "a", value: "Fix price" });
    state = openListingReducer(state, { type: "open", id: "a", tab: "ebay" });
    expect(state).toMatchObject({ reviewTab: "forms", editTab: "ebay", queuedMessage: "Fix price", nonce: 0 });
    expect(openListingReducer(state, { type: "open", id: "a", tab: "general" })).toMatchObject({ reviewTab: "input", editTab: "general" });
  });
  it("switches tabs from the editor", () => {
    let state = openListingReducer(initialOpenListing("a"), { type: "tab", id: "a", value: "mercari" });
    expect(state).toMatchObject({ reviewTab: "forms", editTab: "mercari" });
    state = openListingReducer(state, { type: "tab", id: "a", value: "general" });
    expect(state).toMatchObject({ reviewTab: "input", editTab: "general" });
  });
  it("links to a listing by id", () => {
    expect(listingHref("a b&c")).toBe("?listing=a+b%26c");
  });
  it("leaves modified and non-primary clicks to the browser", () => {
    const click = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false };
    expect(isBrowserLinkClick(click)).toBe(false);
    expect(isBrowserLinkClick({ ...click, metaKey: true })).toBe(true);
    expect(isBrowserLinkClick({ ...click, ctrlKey: true })).toBe(true);
    expect(isBrowserLinkClick({ ...click, button: 1 })).toBe(true);
  });
});

describe("phone pane on open", () => {
  it("opens a listing that already has copy on its form", () => {
    expect(phonePaneOnOpen({ status: "active", price: 38 }, undefined)).toBe("editor");
    expect(phonePaneOnOpen({ status: "sold", price: null }, undefined)).toBe("editor");
    expect(phonePaneOnOpen({ status: "draft", price: 25 }, undefined)).toBe("editor");
    expect(phonePaneOnOpen({ status: "draft", price: null }, { listing: { title: "Jeans", description: "Black" } })).toBe("editor");
  });
  it("opens a draft waiting to be generated on chat", () => {
    expect(phonePaneOnOpen({ status: "draft", price: null }, undefined)).toBe("workspace");
    expect(phonePaneOnOpen({ status: "draft", price: null }, { listing: { title: "New Listing" } })).toBe("workspace");
    expect(phonePaneOnOpen(undefined, undefined)).toBe("workspace");
  });
});
