import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DatabaseUnreachableBanner, UNREACHABLE_TITLE } from "./DatabaseUnreachableBanner";

const store = (state: "ok" | "checking" | "unreachable") => ({ get: () => state, subscribe: () => () => {} });

describe("DatabaseUnreachableBanner", () => {
  it("says the data can't be reached while /api/ready fails", () => {
    const html = renderToStaticMarkup(<DatabaseUnreachableBanner store={store("unreachable")}/>);
    expect(html).toContain("Can&#x27;t reach your data right now — retrying");
    expect(html).toContain('role="alert"');
    expect(UNREACHABLE_TITLE).toMatch(/^Can't reach your data right now/);
  });

  it("shows nothing until /api/ready confirms the outage", () => {
    for (const state of ["ok", "checking"] as const) {
      // Nothing at all, so the startup screen keeps its single status region.
      expect(renderToStaticMarkup(<DatabaseUnreachableBanner store={store(state)}/>)).toBe("");
    }
  });
});
