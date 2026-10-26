import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectionsSettings } from "./ConnectionsSettings";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useQuery,
  useMutation,
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
}));

beforeEach(() => {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: null });
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => {
    if (queryKey[0] === "extension-status") return { data: { connected: true } };
    if (queryKey[0] === "marketplace-sessions") {
      return { data: { sessions: {
        depop: { state: "signed_in", expires_at: null, label: "ada", expiring: false },
        poshmark: { state: "signed_out", expires_at: null, label: null, expiring: false },
        mercari: { state: "expired", expires_at: 10, label: null, expiring: false },
        sellwild: { state: "unknown", expires_at: null, label: null, expiring: false },
        vinted: { state: "signed_out", expires_at: null, label: null, expiring: false },
      } } };
    }
    if (queryKey[0] === "direct-connections") return { data: { accounts: {} } };
    if (queryKey[0] === "settings-tailscale") return { isPending: true };
    return { data: undefined };
  });
});

describe("Connections settings", () => {
  it("groups every marketplace at the top with a sale detection switch", () => {
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    const accounts = html.indexOf('id="accounts"');
    const vendoo = html.indexOf('id="connections"');
    expect(accounts).toBeGreaterThanOrEqual(0);
    expect(accounts).toBeLessThan(vendoo);
    const section = html.slice(accounts, vendoo);
    expect(section).toContain(">Accounts<");
    let cursor = 0;
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop", "Sellwild", "Vinted"]) {
      const at = section.indexOf(name, cursor);
      expect(at).toBeGreaterThanOrEqual(cursor);
      cursor = at + name.length;
    }
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop", "Sellwild"]) {
      expect(section).toContain(`Sale detection for ${name}`);
    }
    expect(section).not.toContain("Sale detection for Vinted");
    expect(section).toContain("Sales on Vinted are not detected");
    expect(section).toContain("Detect sales");
    expect(section).toContain("Where the sign-in lives");
    expect(html).toContain("Marketplaces → eBay → Offers");
    expect(html).toContain('id="ebay-inventory-location"');
    expect(html).toContain("eBay inventory location");
    expect(html).not.toContain("Chrome sign-in");
    expect(html).not.toContain("ebay-offers");
    expect(html).not.toContain("Automatic offers check every 30 minutes");
  });

  it("offers to reconnect eBay when eBay refuses the listing policies", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options: { queryKey: string[] }) => {
      if (options.queryKey[0] === "direct-connections") return { data: { accounts: { ebay: { connected: true, label: "seller" } } } };
      if (options.queryKey[0] === "ebay-listing-profiles") return { error: new Error("Reconnect eBay to grant it.") };
      return base(options);
    });
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    expect(html).toContain("Reconnect eBay to grant it.");
    expect(html).toContain("Reconnect eBay</button>");
  });
});
