import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { WorkspaceRuntimeProvider } from "../../app/runtime";
import { SettingsPage } from "../SettingsPage";
import { ProvidersSettings } from "./ProvidersSettings";

function render(hosted: boolean, primary = "openai") {
  const client = new QueryClient();
  client.setQueryData(["settings-provider"], { primary, fallback: "none", photo_provider: "same" });
  client.setQueryData(["ai-keys"], { providers: [{ id: primary, sign_in: true }] });
  return renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ hosted, features: new Set(), views: new Set(["settings"]), marketplaceForms: new Set() }}>
      <QueryClientProvider client={client}>{hosted ? <SettingsPage section="general" onOpenListing={() => {}} /> : <ProvidersSettings />}</QueryClientProvider>
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
  expect(html).toContain("requires a connected Mac worker");
  expect(html).not.toContain("Retry sign-in");
});

it("keeps desktop provider setup and Keychain guidance", () => {
  expect(render(false)).toContain("macOS Keychain");
});
