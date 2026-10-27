import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeDollarSignIcon, ExternalLinkIcon } from "lucide-react";
import { api } from "../api/client";
import { useListingSessionActive } from "./listingSession";
import type { MarketplaceSale } from "../api/directConnections";
import { marketplaceName } from "./marketplaceNames";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Button, buttonVariants } from "./ui/button";

const SALES_POLL_MS = 15_000;

function soldWhen(sale: MarketplaceSale): string {
  const when = sale.soldAt || sale.receivedAt;
  if (!when) return "";
  const date = new Date(`${when}${when.endsWith("Z") ? "" : "Z"}`);
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function listJoin(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export function SaleNotice({ sale, openInVendoo }: { sale: MarketplaceSale; openInVendoo?: ReactNode }) {
  const queryClient = useQueryClient();
  const soldOn = marketplaceName(sale.marketplace);
  const reversed = sale.orderStatus === "cancelled" || sale.orderStatus === "refunded" || sale.orderStatus === "cancellation_pending";
  const exhausted = !reversed && sale.stockStatus === "sold_out";
  const elsewhere = listJoin(sale.listedElsewhere.map(marketplaceName));
  const done = useMutation({
    mutationFn: () => api.marketplaceSales.dismiss(sale.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["marketplace-sales"] }),
  });

  return (
    <Alert variant={reversed ? "warning" : "success"} role="status" controlAlignment="first-line" className="mx-3 mt-2.5 shrink-0">
      <BadgeDollarSignIcon />
      <AlertTitle>
        {sale.orderStatus === "cancelled" ? "Cancelled" : sale.orderStatus === "refunded" ? "Refunded" : sale.orderStatus === "cancellation_pending" ? "Cancellation pending" : "Sold"} on {soldOn}
        {sale.quantity && sale.quantity > 1 ? ` (${sale.quantity})` : ""}
      </AlertTitle>
      <AlertDescription className="gap-2">
        <p>
          {soldWhen(sale) ? `${soldOn} reported this sale ${soldWhen(sale)}. ` : null}
          {reversed
            ? "The marketplace updated this order. No inventory was restocked or relisted. Review the order and check stock before making changes."
            : sale.stockStatus === "remaining"
            ? `${sale.remainingQuantity} units remain on ${soldOn}. Review quantities on other marketplaces before making changes.`
            : sale.stockStatus === "unknown"
              ? "Remaining stock is not verified. Click Sync to check linked marketplace inventory before deciding what to delist."
              : sale.listedElsewhere.length
                ? `Anteroom still has listings on ${elsewhere}.`
                : "Anteroom has no other marketplace listings recorded."}
        </p>
        {exhausted && sale.listedElsewhere.length ? (
          <p className="text-xs">
            Take it down under <strong>Marketplaces</strong> below: select {elsewhere} and choose <strong>Delist selected…</strong>.
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {exhausted && sale.listedElsewhere.length
            ? openInVendoo ??
              (sale.vendooUrl ? (
                <a className={buttonVariants({ size: "xs", variant: "outline" })} href={sale.vendooUrl} target="_blank" rel="noreferrer">
                  <ExternalLinkIcon />
                  Open in Vendoo
                </a>
              ) : null)
            : null}
          <Button size="xs" variant="ghost" disabled={done.isPending} onClick={() => done.mutate()}>
            Done
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

/** Sales eBay or Etsy reported for this listing, until the seller marks them done. */
export function SoldElsewhereNotice({ convId, openInVendoo }: { convId: string; openInVendoo?: ReactNode }) {
  const sessionActive = useListingSessionActive();
  const { data } = useQuery({
    queryKey: ["marketplace-sales", convId],
    queryFn: () => api.marketplaceSales.list({ conversationId: convId }),
    refetchInterval: sessionActive ? SALES_POLL_MS : false,
  });
  const sales = data?.sales ?? [];
  if (!sales.length) return null;
  return (
    <>
      {sales.map((sale) => (
        <SaleNotice key={sale.id} sale={sale} openInVendoo={openInVendoo} />
      ))}
    </>
  );
}
