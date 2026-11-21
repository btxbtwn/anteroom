import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { HostedSetupChecklistView, SetupChecklist } from "./SetupChecklist";

vi.mock("./ConnectChromeButton", () => ({ ConnectChromeButton: () => <button>Connect Chrome</button> }));
vi.mock("./ExtensionLoadPath", () => ({ ExtensionLoadPath: () => null }));

describe("setup without Chrome", () => {
  it("explains the optional extension while allowing a configured seller to create", () => {
    const html = renderToStaticMarkup(<SetupChecklist providerConfigured chromeAvailable={false}
      extensionConnected={false} onOpenSettings={() => {}} onCreate={() => {}} />);
    expect(html).toContain("Create and generate listings without Chrome");
    expect(html).toContain("Optional: connect desktop Chrome");
    expect(html).toContain("eBay and Etsy use connected accounts");
    expect(html).not.toContain("Vinted");
    expect(html).not.toContain("Sellwild");
    expect(html).not.toMatch(/<button[^>]* disabled=""[^>]*>Create a listing/);
  });
});

describe("hosted first-run checklist", () => {
  it("omits the AI key step when managed AI is enabled and shows Connections + Chrome Bridge guidance", () => {
    const html = renderToStaticMarkup(
      <HostedSetupChecklistView
        managedAI
        providerConfigured
        ebayConnected={false}
        etsyConnected={false}
        onOpenProviders={() => {}}
        onOpenConnections={() => {}}
        onCreate={() => {}}
      />,
    );
    expect(html).not.toContain("Open AI settings");
    expect(html).toContain("Connect eBay or Etsy in Settings.");
    expect(html).toContain("Open Connections");
    expect(html).toContain("Optional: connect Chrome with Anteroom Bridge");
    expect(html).toContain("Create a listing");
    expect(html).not.toContain("ChatGPT");
    expect(html).not.toContain("MiMo");
  });

  it("includes the AI provider step when managed AI is off and hides Open Connections once both eBay and Etsy are connected", () => {
    const html = renderToStaticMarkup(
      <HostedSetupChecklistView
        managedAI={false}
        providerConfigured={false}
        ebayConnected
        etsyConnected
        onOpenProviders={() => {}}
        onOpenConnections={() => {}}
        onCreate={() => {}}
      />,
    );
    expect(html).toContain("Add an AI provider key to generate listings.");
    expect(html).toContain("Open AI settings");
    expect(html).toContain("eBay and Etsy connected.");
    expect(html).not.toContain("Open Connections");
    expect(html).toContain("configure an AI provider key before generating");
  });
});
