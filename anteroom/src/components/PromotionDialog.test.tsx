import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { PromotionDialog } from "./PromotionDialog";

const { useMutation, useQuery, invalidateQueries } = vi.hoisted(() => ({ useMutation: vi.fn(), useQuery: vi.fn(), invalidateQueries: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useMutation, useQuery, useQueryClient: () => ({ invalidateQueries }) }));
vi.mock("./PriceDropParts", () => ({
  PriceDialog: ({ title, children, footer }: { title: string; children: React.ReactNode; footer: React.ReactNode }) => <div><h2>{title}</h2>{children}{footer}</div>,
}));

beforeEach(() => {
  useMutation.mockReset();
  useMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
  useQuery.mockReset();
  useQuery.mockReturnValue({ data: undefined, isPending: false, isSuccess: false });
});

describe("promotion dialog", () => {
  it("names no marketplace or action by default and sends nothing until reviewed", () => {
    const perform = vi.spyOn(api.promotions, "perform");
    const preview = vi.spyOn(api.promotions, "preview");
    const html = renderToStaticMarkup(<PromotionDialog conversationIds={["a", "b"]} onClose={vi.fn()} />);
    expect(html).toContain("Share or send offers for 2 items");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Review items<\/button>/);
    expect(html).not.toMatch(/type="radio"[^>]*checked=""/);
    // Marketplaces Anteroom cannot act on are shown but cannot be chosen.
    const radios = html.match(/<input[^>]*name="promotion-marketplace"[^>]*>/g) || [];
    expect(radios.map((radio) => radio.includes('disabled=""'))).toEqual([false, true, true]);
    expect(perform).not.toHaveBeenCalled();
    expect(preview).not.toHaveBeenCalled();
    for (const [options] of useMutation.mock.calls) expect(options.retry).toBe(false);
  });

  it("lists unconfirmed actions with read-only checks", () => {
    useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => (queryKey[0] === "promotions"
      ? { data: { supported: { poshmark: ["offer", "share"] }, history: [{ id: "p1", state: "unknown", action: "offer", offer_price: "40.00",
        marketplace: "poshmark", title: "Coat", listing_id: "x", note: "Check the listing" }] }, isSuccess: true }
      : { data: undefined, isPending: false, isSuccess: false }));
    const html = renderToStaticMarkup(<PromotionDialog conversationIds={["a"]} onClose={vi.fn()} />);
    expect(html).toContain("Needs a check");
    expect(html).toContain("Check result");
    expect(html).toContain("offer $40.00 on Poshmark");
  });

  it("shows Mercari and Depop offers as built but waiting for a capture", () => {
    useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => (queryKey[0] === "promotions"
      ? { data: { supported: { poshmark: ["offer", "share"], mercari: ["offer"], depop: ["offer"] },
        awaiting_capture: { mercari: ["offer"], depop: ["offer"] }, history: [] }, isSuccess: true }
      : { data: undefined, isPending: false, isSuccess: false }));
    const html = renderToStaticMarkup(<PromotionDialog conversationIds={["a"]} onClose={vi.fn()} />);
    const radios = html.match(/<input[^>]*name="promotion-marketplace"[^>]*>/g) || [];
    expect(radios.map((radio) => radio.includes('disabled=""'))).toEqual([false, true, true]);
    expect(html.match(/needs one capture/g)?.length).toBe(2);
  });
});
