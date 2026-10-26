import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { MarketplaceSale } from "../api/directConnections";
import { SaleNotice } from "./SoldElsewhereNotice";

function notice(stockStatus: MarketplaceSale["stockStatus"], remainingQuantity: number | null = null, orderStatus: MarketplaceSale["orderStatus"] = "paid") {
  const sale: MarketplaceSale = { id: "sale", marketplace: "etsy", orderId: "order", orderStatus, listingId: "555", quantity: 1,
    stockStatus, remainingQuantity, state: "new", soldAt: null, receivedAt: null, conversationId: "coat", title: "Coat",
    listedElsewhere: ["ebay"], vendooUrl: null };
  return renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}><SaleNotice sale={sale} /></QueryClientProvider>);
}
describe("quantity-aware sale alerts", () => {
  it("shows remaining stock without instructing the seller to delist the whole item", () => {
    const html = notice("remaining", 3);
    expect(html).toContain("3 units remain on Etsy");
    expect(html).toContain("Review quantities");
    expect(html).not.toContain("Take it down");
    expect(html).not.toContain("Vendoo still shows");
  });
  it("asks for verification when a partial sale has no fresh stock read", () => {
    const html = notice("unknown");
    expect(html).toContain("Remaining stock is not verified");
    expect(html).toContain("Click Sync");
    expect(html).not.toContain("Take it down");
  });
  it("keeps seller-confirmed delisting guidance for exhausted stock", () => {
    const html = notice("sold_out", 0);
    expect(html).toContain("Anteroom still has listings on eBay");
    expect(html).toContain("Delist selected");
  });
});

it("shows cancelled orders as review notices without suggesting restocking or delisting", () => {
  const html = notice("unknown", null, "cancelled");
  expect(html).toContain("Cancelled");
  expect(html).toContain("No inventory was restocked or relisted");
  expect(html).not.toContain("Take it down");
});
