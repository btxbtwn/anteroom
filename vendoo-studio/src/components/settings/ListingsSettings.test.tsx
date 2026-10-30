import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import { SettingsPage } from "../SettingsPage";

it("opens the shared listing controls in the hosted workspace without Vendoo automation setup", () => {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { vendoo_enabled: false, recent_vendoo_labels: ["Closet"], label_colors: { Closet: "teal" }, stale_listing_days: 45 });
  client.setQueryData(["listing-templates"], { templates: [] });
  client.setQueryData(["settings-marketplaces"], { available: [{ id: "depop", label: "Depop", fillable: true }], selected: ["depop"] });
  client.setQueryData(["settings-package-dimensions"], { length: 12, width: 10, height: 3 });
  client.setQueryData(["settings-ships-from-zip"], { zip: "70125" });
  client.setQueryData(["settings-hidden-fields"], { always: [{ marketplace: "poshmark", field: "style tags", label: "Style tags" }], listing: [] });
  client.setQueryData(["settings-formulas"], { title: "Shop title", description: "Shop description" });
  const html = renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(["depop", "poshmark"]) }}>
      <QueryClientProvider client={client}><SettingsPage section="listings" onOpenListing={() => {}} /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
  for (const text of ["Saved templates", "Where you list", "Depop", "Closet", "Shipping defaults", "70125", "Style tags", "Show", "Shop title", "Shop description", "Find similar items"]) {
    expect(html).toContain(text);
  }
  expect(html).not.toContain("Send to Vendoo");
  expect(html).not.toContain("Duplicate Vendoo links");
  expect(html).not.toContain("private vault");
});
