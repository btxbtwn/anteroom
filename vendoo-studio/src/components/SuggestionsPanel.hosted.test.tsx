import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { WorkspaceRuntimeProvider, type WorkspaceFeature } from "../app/runtime";
import { SuggestionsPanel } from "./SuggestionsPanel";

// Static rendering has no focused browser document; keep the query timing pure.
vi.mock("../api/polling", async (original) => ({
  ...await original<typeof import("../api/polling")>(),
  pollMs: (milliseconds: number) => milliseconds,
}));

function panel(markdowns: boolean, failed = false) {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { vendoo_enabled: false });
  client.setQueryData(["suggestions"], { suggestions: [{
    conversation_id: "coat", title: "Own stale coat", kind: "stale_active", reason: "Listed 70 days ago",
    action: "open", score: 200, age_days: 70, similar_sold: 0, season_fit: null,
  }] });
  if (failed) client.getQueryCache().find({ queryKey: ["suggestions"] })!.setState({
    status: "error", error: new Error("Connection interrupted"),
  });
  const features = new Set<WorkspaceFeature>(["suggestions", ...(markdowns ? ["bulk_markdowns" as const] : [])]);
  return renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted: true, features, views: new Set(["listings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
      <QueryClientProvider client={client}><SuggestionsPanel variant="workspace" onSelect={() => {}} /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
}

it("shows the inventory rules and reviewed markdown entry without a generation provider", () => {
  const html = panel(true);
  expect(html).toContain("Own stale coat");
  expect(html).toContain("Mark down");
  expect(html).not.toContain("Send to Vendoo");
});

it("keeps the feed usable when markdown actions are unavailable", () => {
  const html = panel(false);
  expect(html).toContain("Own stale coat");
  expect(html).not.toContain("Mark down");
});

it("reports a failed refresh while keeping previously loaded suggestions", () => {
  const html = panel(true, true);
  expect(html).toContain("Connection interrupted");
  expect(html).toContain("Own stale coat");
  expect(html).not.toContain("No suggestions right now");
});
