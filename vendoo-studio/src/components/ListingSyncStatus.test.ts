import { describe, expect, it } from "vitest";
import { checkedAgo, directSyncErrors, hasDirectListings } from "./ListingSyncStatus";

describe("checkedAgo", () => {
  const now = Date.parse("2026-09-19T12:00:00Z");

  it("says just now inside a minute", () => {
    expect(checkedAgo("2026-09-19T11:59:30Z", now)).toBe("just now");
  });

  it("counts minutes, then hours", () => {
    expect(checkedAgo("2026-09-19T11:55:00Z", now)).toBe("5m ago");
    expect(checkedAgo("2026-09-19T09:00:00Z", now)).toBe("3h ago");
  });

  it("returns nothing for an unreadable stamp", () => {
    expect(checkedAgo("soon", now)).toBe("");
  });
});

describe("hasDirectListings", () => {
  const live = { url: "u", listing_id: "1", sold_here: false, state: "live" } as const;

  it("counts listings Anteroom made or the seller linked, not Vendoo's", () => {
    expect(hasDirectListings({}, { depop: { ...live, source: "anteroom" } })).toBe(true);
    expect(hasDirectListings({}, { ebay: { ...live, source: "seller" } })).toBe(true);
    expect(hasDirectListings({}, { ebay: { ...live, source: "vendoo" } })).toBe(false);
    expect(hasDirectListings({}, { depop: { ...live, source: "anteroom", sold_here: true } })).toBe(false);
  });

  it("counts a direct action that is still unconfirmed", () => {
    expect(hasDirectListings({ mercari: { state: "unknown", job_id: "j" } }, {})).toBe(true);
    expect(hasDirectListings({ mercari: { state: "draft", job_id: "j" } }, {})).toBe(false);
    expect(hasDirectListings(undefined, undefined)).toBe(false);
  });
});

describe("directSyncErrors", () => {
  it("names each marketplace that could not be checked", () => {
    expect(directSyncErrors({
      checked_at: "2026-10-25T12:00:00Z",
      marketplaces: { depop: { ok: false, error: "Connect Chrome." }, ebay: { ok: true, error: null } },
    })).toEqual(["Depop: Connect Chrome."]);
    expect(directSyncErrors(undefined)).toEqual([]);
  });
});
