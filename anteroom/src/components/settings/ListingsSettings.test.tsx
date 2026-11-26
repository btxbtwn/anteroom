import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import { SettingsPage } from "../SettingsPage";

it("opens the shared listing controls in the hosted workspace", () => {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { recent_vendoo_labels: ["Closet"], label_colors: { Closet: "teal" }, stale_listing_days: 45 });
  client.setQueryData(["listing-templates"], { templates: [] });
  client.setQueryData(["settings-marketplaces"], { available: [{ id: "depop", label: "Depop", fillable: true }], selected: ["depop"] });
  client.setQueryData(["settings-package-dimensions"], { length: 12, width: 10, height: 3 });
  client.setQueryData(["settings-ships-from-zip"], { zip: "70125" });
  client.setQueryData(["settings-hidden-fields"], {
    always: [
      { marketplace: "poshmark", field: "style tags", label: "Style tags" },
      { marketplace: "ebay", field: "best_offer", label: "Best offer" },
      { marketplace: "general", field: "notes", label: "Notes" },
    ],
    listing: [],
  });
  client.setQueryData(["settings-formulas"], { title: "Shop title", description: "Shop description" });
  const html = renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(["depop", "poshmark"]), actionMarketplaces: new Set() }}>
      <QueryClientProvider client={client}><SettingsPage section="listings" onOpenListing={() => {}} /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
  for (const text of ["Saved templates", "Where you list", "Depop", "Closet", "Shipping defaults", "70125", "Style tags", "eBay", "Poshmark", "General", "Show", "Shop title", "Shop description", "Find similar items"]) {
    expect(html).toContain(text);
  }
  expect(html).not.toContain('capitalize text-muted-foreground">ebay');
  expect(html).not.toContain("Send to Vendoo");
  expect(html).not.toContain("Duplicate Vendoo links");
  expect(html).not.toContain("private vault");
});

it("starts from the current description sections and lists the extras", () => {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { recent_vendoo_labels: [], label_colors: {}, stale_listing_days: 45 });
  client.setQueryData(["listing-templates"], { templates: [] });
  client.setQueryData(["settings-marketplaces"], { available: [], selected: [] });
  client.setQueryData(["settings-package-dimensions"], { length: 12, width: 10, height: 3 });
  client.setQueryData(["settings-ships-from-zip"], { zip: "" });
  client.setQueryData(["settings-hidden-fields"], { always: [], listing: [] });
  client.setQueryData(["settings-formulas"], {
    title: "",
    description: "",
    default_title: "{BRAND} {SIZE} {VIBE} {ITEM} {COLOR} {FIT}",
    default_description: "{trendy vibe/style keyword sentence with period}\n\nFlaws:\n- {one flaw per line}\nSee photos for details.\n\nMeasurements: {See photos OR specific measurements}",
    empty_description: "{one short factual description with no labeled sections}",
    custom_description: false,
    blocks: [
      { id: "vibe", label: "Vibe", detail: "One short sentence of style and trend keywords.", snippet: "{trendy vibe/style keyword sentence with period}", default: true, enabled: true },
      { id: "material", label: "Material", detail: "Fiber from the label, with blend percentages.", snippet: "Material: {fiber from the label}", default: false, enabled: false },
      { id: "flaws", label: "Flaws", detail: "Each flaw on its own line.", snippet: "Flaws:\n- {one flaw per line}\nSee photos for details.", default: true, enabled: true },
      { id: "measurements", label: "Measurements", detail: "Flat measurements, or See photos.", snippet: "Measurements: {See photos OR specific measurements}", default: true, enabled: true },
    ],
  });
  const html = renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
      <QueryClientProvider client={client}><SettingsPage section="listings" onOpenListing={() => {}} /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
  expect(html).toContain("Vibe");
  expect(html).toContain("Material");
  expect(html).toContain("Flaws");
  expect(html).toContain("Measurements");
  expect(html).toContain("{trendy vibe/style keyword sentence with period}");
  expect(html).not.toContain("Material: {fiber from the label}");
  expect(html).toContain('aria-label="Drag to reorder Vibe"');
  expect(html).toContain('aria-label="Add Material"');
});
