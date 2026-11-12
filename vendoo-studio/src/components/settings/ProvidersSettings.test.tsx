import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import { SettingsPage } from "../SettingsPage";
import { ProvidersSettings } from "./ProvidersSettings";

function render(hosted: boolean, primary = "openai") {
  const client = new QueryClient();
  client.setQueryData(["settings-provider"], { primary, fallback: "none", photo_provider: "same" });
  client.setQueryData(["ai-keys"], { providers: [{ id: primary, sign_in: true }] });
  return renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}>
      <QueryClientProvider client={client}>{hosted ? <SettingsPage section="providers" onOpenListing={() => {}} /> : <ProvidersSettings />}</QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
}

it("uses the private account vault for hosted keys and omits local sign-in controls", () => {
  const html = render(true, "openrouter");
  expect(html).toContain("private vault");
  expect(html).not.toContain("macOS Keychain");
  expect(html).not.toContain("Sign in with OpenRouter");
  expect(html).not.toContain("Brave Search");
});

it("preserves a desktop provider preference without offering a local sign-in on the server", () => {
  const html = render(true, "chatgpt");
  expect(html).toContain("Open Anteroom on your Mac");
  expect(html).toContain("Keep the Mac awake");
  expect(html).not.toContain("Retry sign-in");
});

it("keeps desktop provider setup and Keychain guidance", () => {
  expect(render(false)).toContain("macOS Keychain");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it("lets the Mac app's account window sign this Mac into ChatGPT", () => {
  const ok = () => Promise.resolve({ ok: true });
  vi.stubGlobal("window", {
    pywebview: { api: { chatgpt_sign_in: ok, chatgpt_sign_in_status: ok, chatgpt_sign_in_cancel: ok } },
    addEventListener: () => {},
    removeEventListener: () => {},
  });
  const html = render(true, "chatgpt");
  expect(html).toContain("Sign in with ChatGPT");
  expect(html).toContain("the sign-in stays on this Mac");
  expect(html).not.toContain("Open Anteroom on your Mac");
});
