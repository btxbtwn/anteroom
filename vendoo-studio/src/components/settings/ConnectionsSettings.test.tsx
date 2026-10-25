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
      } } };
    }
    if (queryKey[0] === "direct-connections") return { data: { accounts: {} } };
    if (queryKey[0] === "settings-tailscale") return { isPending: true };
    return { data: undefined };
  });
});

describe("Connections settings", () => {
  it("keeps the eBay sign-in and points offers at the eBay marketplace tab", () => {
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    expect(html).toContain("Sale alerts");
    expect(html).toContain("Marketplaces → eBay → Offers");
    expect(html).not.toContain("ebay-offers");
    expect(html).not.toContain("Automatic offers check every 30 minutes");
  });
});
