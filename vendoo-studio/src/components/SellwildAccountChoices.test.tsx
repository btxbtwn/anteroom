import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SellwildAccountChoices } from "./SellwildAccountChoices";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";

vi.mock("../api/client", () => ({ api: { nativeMarketplaces: { sellwildConnect: vi.fn() } } }));

function render(hosted: boolean, connected: boolean) {
  return renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted }}>
      <QueryClientProvider client={new QueryClient()}>
        <SellwildAccountChoices convId="conversation" connected={connected} />
      </QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
}

describe("Sellwild account choices", () => {
  it.each([true, false])("on the Mac, shows the sign-in prompt only while disconnected (%s)", (connected) => {
    const html = render(false, connected);
    expect(html.includes("Sign in to Sellwild in Anteroom")).toBe(!connected);
    expect(html.includes("Sellwild is signed in.")).toBe(connected);
    expect(html).not.toContain("Reload");
  });

  it("on the web, sends the seller to sign in to Sellwild in their own Chrome, then reload", () => {
    const html = render(true, false);
    expect(html).toContain("sign-in in Chrome.");
    expect(html).toContain('href="https://sellwild.com/"');
    expect(html).toContain("Sign in to Sellwild in Chrome</a>");
    expect(html).toContain("Reload Sellwild choices</button>");
    expect(html).not.toContain("in Anteroom");
    expect(render(true, true)).toContain("Sellwild is signed in.");
  });
});
