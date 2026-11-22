import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import { SidebarFooter } from "./SidebarFooter";

const noop = () => {};

function footer(activeView: "home" | "marketplaces" | "listings" = "listings") {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(), views: new Set(["home", "assistant", "sourcing", "analytics", "marketplaces", "automations", "settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
        <SidebarFooter settingsMode={false} activeView={activeView} onCloseSettings={noop} onOpenHome={noop} onOpenSourcing={noop}
          onOpenAnalytics={noop} onOpenMarketplaces={noop} onOpenAutomations={noop} onOpenAssistant={noop} onOpenSettings={noop} />
      </WorkspaceRuntimeProvider>
    </QueryClientProvider>,
  );
}

it("names every page in a labelled navigation", () => {
  const html = footer();
  expect(html).toContain('<nav aria-label="Pages"');
  for (const label of ["Today", "Assistant", "Sourcing", "Analytics", "Marketplaces", "Automations", "Settings"]) {
    expect(html).toContain(`aria-label="${label}"`);
    expect(html).toContain(`>${label}</span>`);
  }
});

it("marks the open page as current", () => {
  expect(footer("home")).toMatch(/aria-current="page"[^>]*aria-label="Today"|aria-label="Today"[^>]*aria-current="page"/);
  expect(footer("marketplaces")).toMatch(/aria-current="page"[^>]*aria-label="Marketplaces"|aria-label="Marketplaces"[^>]*aria-current="page"/);
});
