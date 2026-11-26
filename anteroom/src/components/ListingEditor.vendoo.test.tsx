import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ListingEditor, StructuredEditor, editorCopy } from "./ListingEditor";

// "pending" and "failed" are the moments before the editor knows its mode.
const { mode } = vi.hoisted(() => ({ mode: { value: "direct" as string } }));
vi.mock("@tanstack/react-query", async (original) => ({
  ...await original<typeof import("@tanstack/react-query")>(),
  useQuery: ({ queryKey }: { queryKey: string[] }) => queryKey[0] === "settings-ui"
    ? mode.value === "pending" ? { data: undefined, isPending: true, isFetching: true, error: null }
      : mode.value === "failed" ? { data: undefined, isPending: false, isFetching: false, error: new Error("Settings could not be read.") }
      : { data: { listing_mode: mode.value }, isPending: false, error: null }
    : { data: undefined, isPending: false, isFetching: false, isError: false, error: null },
  useMutation: () => ({ mutate: vi.fn(), isPending: false, error: null, variables: undefined }),
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn(), getQueryData: vi.fn() }),
}));

vi.mock("./chat/liveChatStore", async (original) => ({
  ...await original<typeof import("./chat/liveChatStore")>(),
  useChatBusy: () => false,
}));

function editor(state: "pending" | "failed") {
  mode.value = state;
  return renderToStaticMarkup(
    <ListingEditor convId="item" reviewTab="forms" onReviewTabChange={() => {}} editTab="general" onEditTabChange={() => {}} />,
  );
}

function render(listingMode: "vendoo" | "direct") {
  mode.value = listingMode;
  return renderToStaticMarkup(
    <StructuredEditor convId="item" listing={{ title: "Tee" }} tab="general" target={null} onChange={() => {}} agentLocks={["title"]} />,
  );
}

// The seller asked for the Vendoo listing mode to stay exactly as it is. Every
// field and status message the direct editor reworded keeps main's wording there.
describe("Vendoo listing mode wording", () => {
  it("keeps main's wording for every message the direct editor reworded", () => {
    const copy = editorCopy(true);
    expect(copy.savingAfterAnt).toBe("Edits kept. Waiting for chat to finish before saving…");
    expect(copy.agentEditing).toBe("Agent editing…");
    expect(copy.noExactMatch("eBay")).toBe("Suggested: check it. No exact eBay match.");
    expect(copy.categoryFormMissing("Etsy")).toBe("The full category form is unavailable. Reference choices are shown where available.");
    expect(copy.choicesMissing("Etsy")).toBe("Choices unavailable. Reopen the listing to load this field.");
  });

  it("shows main's loading line while the editor doesn't know its mode yet", () => {
    const html = editor("pending");
    expect(html).toContain('<p role="status" class="p-3 text-xs text-muted-foreground">Loading listing workflow…</p>');
    expect(html).not.toContain('data-slot="skeleton"');
  });

  it("shows main's error line, without Retry, when the mode setting fails to load", () => {
    const html = editor("failed");
    expect(html).toContain('<p role="alert" class="p-3 text-xs text-destructive-foreground">Settings could not be read.</p>');
    expect(html).not.toContain("The editor didn&#x27;t load");
    expect(html).not.toContain(">Retry<");
  });

  it("shows main's agent-editing status in that mode and the new one in the direct editor", () => {
    const vendoo = render("vendoo");
    expect(vendoo).toContain("Agent editing…");
    expect(vendoo).not.toContain("Ant is editing this field…");
    const direct = render("direct");
    expect(direct).toContain("Ant is editing this field…");
    expect(direct).not.toContain("Agent editing…");
  });
});
