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
  expect(html).not.toContain("Use Vendoo");
  expect(client.getQueryCache().find({ queryKey: ["updates"] })).toBeUndefined();
  expect(client.getQueryCache().find({ queryKey: ["update-progress"] })).toBeUndefined();
});

it.each([false, true])("opens Experimental with the saved Vendoo choice (hosted: %s)", (hosted) => {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { listing_mode: "vendoo" });
  const html = renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
      <QueryClientProvider client={client}><SettingsPage section="experimental" onOpenListing={() => {}} /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
  expect(html).toContain("Use Vendoo");
  expect(html).toContain('aria-checked="true"');
  expect(html).toContain("Hosted marketplace browser");
  expect(html).toContain("Coming soon");
  expect(client.getQueryData(["settings-ui"])).toEqual({ listing_mode: "vendoo" });
});

it("describes system appearance for the current device", () => {
  const client = new QueryClient();
  client.setQueryData(["settings-ui"], { theme: "system", listing_names: "title" });
  const html = renderToStaticMarkup(<WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}><QueryClientProvider client={client}><SettingsPage section="general" onOpenListing={() => {}} /></QueryClientProvider></WorkspaceRuntimeProvider>);
  expect(html).toContain("System follows your device’s light or dark setting.");
});
