// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { ShippingPreset, ShippingPresets } from "../../api/listingRules";
import { ToastProvider } from "../ui/toast";
import { ShippingPresetsSection } from "./ListingRulesSettings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  vi.restoreAllMocks();
});

const samplePreset: ShippingPreset = {
  id: "preset-1",
  name: "Poly mailer",
  usage_condition: "Light shirts under 1 lb",
  length: 12,
  width: 10,
  height: 2,
  weight_lb: 0,
  weight_oz: 8,
  marketplaces: { ebay: { shipping_service: "USPSGroundAdvantage" } },
};

describe("ShippingPresetsSection removal", () => {
  it("shows an Undo toast after deleting a preset and restores it on Undo", async () => {
    const emptyPayload: ShippingPresets = { ok: true, presets: [], fields: {} };
    const restoredPayload: ShippingPresets = { ok: true, presets: [{ ...samplePreset, id: "preset-2" }], fields: {} };
    const deleteSpy = vi.spyOn(api.settings, "deleteShippingPreset").mockResolvedValue(emptyPayload);
    const createSpy = vi.spyOn(api.settings, "createShippingPreset").mockResolvedValue(restoredPayload);

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
    });
    client.setQueryData(["settings-shipping-presets"], { ok: true, presets: [samplePreset], fields: {} });

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <ToastProvider>
              <ShippingPresetsSection />
            </ToastProvider>
          </QueryClientProvider>,
        );
      });

      const deleteButton = host.querySelector<HTMLButtonElement>('[aria-label="Delete Poly mailer"]');
      expect(deleteButton).not.toBeNull();
      await act(async () => {
        deleteButton!.click();
      });

      await vi.waitFor(() => {
        expect(deleteSpy).toHaveBeenCalledWith("preset-1");
        expect(host.textContent).not.toContain("Poly mailer");
      });

      const undoButton = [...document.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Undo",
      );
      expect(undoButton).toBeDefined();
      await act(async () => {
        undoButton!.click();
      });

      await vi.waitFor(() => {
        expect(createSpy).toHaveBeenCalledWith({
          name: "Poly mailer",
          usage_condition: "Light shirts under 1 lb",
          length: 12,
          width: 10,
          height: 2,
          weight_lb: 0,
          weight_oz: 8,
          marketplaces: { ebay: { shipping_service: "USPSGroundAdvantage" } },
        });
        expect(host.textContent).toContain("Poly mailer");
      });
    } finally {
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    }
  });
});
