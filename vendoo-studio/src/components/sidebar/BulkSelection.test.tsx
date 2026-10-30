import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { WorkspaceRuntimeProvider, type WorkspaceFeature } from "../../app/runtime";
import type { BulkRegenerate } from "../useBulkRegenerate";
import type { ListingSelection } from "./useListingSelection";
import { BulkActionBar } from "./BulkSelection";

function bar(hosted: boolean, edits = false) {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { vendoo_enabled: !hosted });
  const content = <BulkActionBar bulk={{ run: null, running: false } as BulkRegenerate}
    selection={{ selectedIds: new Set(["coat"]), regenerateIds: ["coat"] } as ListingSelection} eligibleIds={["coat"]} />;
  return renderToStaticMarkup(<QueryClientProvider client={client}>{hosted
    ? <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set<WorkspaceFeature>(["bulk_actions", "bulk_copy", "bulk_markdowns", ...(edits ? ["bulk_edit" as const] : [])]), views: new Set(["listings"]), marketplaceForms: new Set() }}>{content}</WorkspaceRuntimeProvider>
    : content}</QueryClientProvider>);
}

it("offers only the supported reviewed bulk actions on the web", () => {
  const html = bar(true);
  expect(html).toContain("Copy items");
  expect(html).toContain("Mark down");
  for (const label of ["Edit fields", "Marketplaces", "Share / offers", "Delete", "Regenerate"]) expect(html).not.toContain(label);
});

it("retains all desktop bulk actions", () => {
  const html = bar(false);
  for (const label of ["Copy items", "Mark down", "Edit fields", "Marketplaces", "Share / offers", "Delete", "Regenerate"]) expect(html).toContain(label);
});

it("opens the field review entry when the account runtime supports it", () => {
  const html = bar(true, true);
  expect(html).toContain("Edit fields");
  for (const label of ["Marketplaces", "Share / offers", "Delete", "Regenerate"]) expect(html).not.toContain(label);
});
