import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NativeDrafts, NativeMarketplace } from "../api/nativeMarketplaces";
import { NativeMarketplaceActions } from "./NativeMarketplaceActions";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useQuery, useMutation,
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

function render(data: NativeDrafts, error: Error | null = null, pending = false) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => queryKey[0] === "native-marketplaces"
    ? { data, error, isError: Boolean(error), isPending: false }
    : { data: { shipping: [], processing: [] }, isPending: false });
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: pending, variables: { marketplace: "depop", kind: "draft" } });
  return renderToStaticMarkup(<NativeMarketplaceActions convId="item" revisionId="current" title="Review this item" disabled={false} />);
}

function row(html: string, name: string) {
  return html.split(`aria-label="${name} listing actions"`)[1].split('role="group"')[0];
}

function publishDisabled(html: string) {
  return /<button[^>]*disabled=""[^>]*>Publish…<\/button>/.test(html);
}

describe("direct marketplace listing controls", () => {
  beforeEach(() => vi.clearAllMocks());

  it.each([
    ["poshmark", "Poshmark"], ["mercari", "Mercari"], ["depop", "Depop"],
  ] as const)("keeps %s publication disabled until a draft is saved", (marketplace: NativeMarketplace, name: string) => {
    expect(publishDisabled(row(render({}), name))).toBe(true);
    const ready = row(render({ [marketplace]: { state: "draft", revision_id: "current", draft_url: "https://example.test/draft" } }), name);
    expect(ready).toContain("Draft saved.");
    expect(ready).toContain("Open draft");
    expect(publishDisabled(ready)).toBe(false);
  });

  it("explains why edits must be saved before publication", () => {
    const html = row(render({ depop: { state: "draft", revision_id: "older" } }), "Depop");
    expect(html).toContain("Save changes");
    expect(html).toContain("The listing changed. Save it again before publishing.");
    expect(publishDisabled(html)).toBe(true);
  });

  it("requires Sellwild review after the local save", () => {
    const saved = { state: "draft", storage: "local", revision_id: "current" } as const;
    expect(publishDisabled(row(render({ sellwild: saved }), "Sellwild"))).toBe(true);
    expect(publishDisabled(row(render({ sellwild: { ...saved, review_ready: true } }), "Sellwild"))).toBe(false);
  });

  it("offers a result check instead of repeating an uncertain write", () => {
    const html = row(render({ mercari: { state: "unknown", revision_id: "current" } }), "Mercari");
    expect(html).toContain("Check result");
    expect(html).toContain("Check before trying again.");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save draft<\/button>/);
    expect(publishDisabled(html)).toBe(true);
  });

  it("blocks writes when the saved marketplace state could not be loaded", () => {
    const html = render({}, new Error("Could not load saved marketplace drafts."));
    expect(html).toContain("Could not load saved marketplace drafts.");
    for (const name of ["Poshmark", "Mercari", "Depop", "Sellwild"]) {
      expect(publishDisabled(row(html, name))).toBe(true);
      expect(row(html, name)).toMatch(/<button[^>]*disabled=""[^>]*>Save (?:draft|in Anteroom)<\/button>/);
    }
  });

  it("blocks another marketplace action while one is submitting", () => {
    const html = render({ poshmark: { state: "draft", revision_id: "current" } }, null, true);
    for (const name of ["Poshmark", "Mercari", "Depop", "Sellwild"]) {
      expect(publishDisabled(row(html, name))).toBe(true);
    }
  });
});
