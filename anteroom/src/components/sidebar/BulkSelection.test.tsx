import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { WorkspaceRuntimeProvider, type WorkspaceFeature } from "../../app/runtime";
import type { BulkRegenerate } from "../useBulkRegenerate";
import type { ListingSelection } from "./useListingSelection";
import { BulkActionBar } from "./BulkSelection";

function bar(hosted: boolean, edits = false, selected: string[] = ["coat"]) {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { vendoo_enabled: !hosted });
  const content = <BulkActionBar bulk={{ run: null, running: false } as BulkRegenerate}
    selection={{ selectedIds: new Set(selected), regenerateIds: selected } as ListingSelection} eligibleIds={["coat", "hat"]} />;
  return renderToStaticMarkup(<QueryClientProvider client={client}>{hosted
    ? <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set<WorkspaceFeature>(["bulk_actions", "bulk_copy", "bulk_markdowns", ...(edits ? ["bulk_edit" as const] : [])]), views: new Set(["listings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>{content}</WorkspaceRuntimeProvider>
    : content}</QueryClientProvider>);
}

it("offers only the supported reviewed bulk actions on the web", () => {
  const html = bar(true);
  expect(html).toContain("Copy items");
  expect(html).toContain("Mark down");
  for (const label of ["Edit fields", "Marketplaces", "Share / offers", "Delete", "Regenerate"]) expect(html).not.toContain(label);
});

it("keeps the common desktop bulk actions in the bar and the rest under More", () => {
  const html = bar(false);
  for (const label of ["Copy items", "Mark down", "Edit fields", "Marketplaces", ">More<"]) expect(html).toContain(label);
  // Share / offers, Regenerate and Delete open from More, so the bar fits the sidebar.
  for (const label of ["Share / offers", "Regenerate", "Delete"]) expect(html).not.toContain(label);
});

/** Each button in the bar's action row, with whether it is disabled. */
function actionButtons(html: string): { label: string; disabled: boolean }[] {
  const row = html.slice(html.lastIndexOf('<div class="flex flex-wrap'));
  return [...row.matchAll(/<button([^>]*)>(.*?)<\/button>/g)].map(([, attrs, inner]) => ({
    label: inner.replace(/<[^>]+>/g, ""),
    disabled: / disabled=""/.test(attrs) || /data-disabled/.test(attrs),
  }));
}

it("disables every bulk action until a listing is selected, and says how many are", () => {
  const empty = bar(false, false, []);
  expect(empty).toContain("0 selected");
  const buttons = actionButtons(empty);
  expect(buttons.map((button) => button.label)).toEqual(["Marketplaces", "Edit fields", "Mark down", "Copy items", "More"]);
  expect(buttons.every((button) => button.disabled)).toBe(true);
  const one = bar(false, false, ["coat"]);
  expect(one).toContain("1 selected");
  expect(actionButtons(one).every((button) => !button.disabled)).toBe(true);
});

it("styles nothing in the bar as destructive", () => {
  // Delete, the one destructive action, is a destructive item inside More.
  expect(bar(false)).not.toMatch(/bg-destructive|text-destructive/);
});

it("opens the field review entry when the account runtime supports it", () => {
  const html = bar(true, true);
  expect(html).toContain("Edit fields");
  for (const label of ["Marketplaces", "Share / offers", "Delete", "Regenerate"]) expect(html).not.toContain(label);
});
