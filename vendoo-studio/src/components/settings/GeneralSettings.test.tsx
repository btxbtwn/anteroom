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
