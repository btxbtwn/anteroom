import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import { SettingsPage } from "../SettingsPage";

it("opens appearance and web release controls without starting the desktop updater", () => {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { theme: "dark", listing_names: "sku" });
  client.setQueryData(["status"], { version: "99.0.0" });
  const html = renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
      <QueryClientProvider client={client}><SettingsPage section="general" onOpenListing={() => {}} /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
  for (const text of ["Appearance", "Theme", "Listing names", "SKU", "About", "99.0.0", "Reload app", "View changelog"]) {
    expect(html).toContain(text);
  }
  expect(html).not.toContain("First-run tutorial");
  expect(html).not.toContain("quit and reopen");
  expect(client.getQueryCache().find({ queryKey: ["updates"] })).toBeUndefined();
  expect(client.getQueryCache().find({ queryKey: ["update-progress"] })).toBeUndefined();
});

it("explains the theme and the Vendoo switch in seller words, with the switch at the row's end on phones", () => {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { theme: "system", listing_names: "title", listing_mode: "direct" });
  client.setQueryData(["status"], { version: "99.0.0" });
  const html = renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
      <QueryClientProvider client={client}><SettingsPage section="general" onOpenListing={() => {}} /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
  expect(html).toContain("System follows your device’s light or dark setting.");
  expect(html).not.toContain("Mac");
  expect(html).not.toContain("Use Vendoo");
  expect(html).toContain("List through Vendoo");
  expect(html).toContain("Only if you have a Vendoo account.");
  const vendoo = html.slice(html.indexOf("List through Vendoo") - 600, html.indexOf("List through Vendoo"));
  expect(vendoo).toContain("data-inline-control");
});
