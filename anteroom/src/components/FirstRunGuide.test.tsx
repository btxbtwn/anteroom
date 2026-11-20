// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { FirstRunGuide } from "./FirstRunGuide";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

it.each(["Connect Chrome", "How Anteroom works"])("uses supported controls and marketplaces in %s", async step => {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  client.setQueryData(["settings-provider"], { primary: "mimo", masked_key: "•••• fixture" });
  client.setQueryData(["ai-keys"], { providers: [] });
  client.setQueryData(["settings-brave"], { configured: false });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><FirstRunGuide providerConfigured chromeAvailable={false} extensionConnected={false} onClose={vi.fn()} onCreateListing={() => false} /></QueryClientProvider>));
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".setup-guide-rail button")].find(button => button.textContent?.includes(step))!.click());
    const text = host.querySelector(".setup-guide-main")!.textContent!;
    if (step === "Connect Chrome") {
      for (const name of ["Poshmark", "Mercari", "Depop"]) expect(text).toContain(name);
      expect(text).not.toContain("Vinted");
      expect(text).not.toContain("Sellwild");
    } else {
      expect(text).toContain("New listing");
      expect(text).not.toContain("Compose starts");
    }
  } finally {
    await act(async () => root.unmount());
    client.clear(); host.remove();
  }
});
