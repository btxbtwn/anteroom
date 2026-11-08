import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SetupChecklist } from "./SetupChecklist";

vi.mock("./ConnectChromeButton", () => ({ ConnectChromeButton: () => <button>Connect Chrome</button> }));
vi.mock("./ExtensionLoadPath", () => ({ ExtensionLoadPath: () => null }));

describe("setup without Chrome", () => {
  it("explains the optional extension while allowing a configured seller to create", () => {
    const html = renderToStaticMarkup(<SetupChecklist providerConfigured chromeAvailable={false}
      extensionConnected={false} onOpenSettings={() => {}} onCreate={() => {}} />);
    expect(html).toContain("Create and generate listings without Chrome");
    expect(html).toContain("Optional: connect desktop Chrome");
    expect(html).toContain("eBay and Etsy use connected accounts");
    expect(html).not.toMatch(/<button[^>]* disabled=""[^>]*>Create a listing/);
  });
});
