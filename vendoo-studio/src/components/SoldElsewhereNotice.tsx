import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeDollarSignIcon } from "lucide-react";
import { api } from "../api/client";
import type { MarketplaceSale } from "../api/directConnections";
import { confirmDialog } from "../ui/confirmDialog";
import { marketplaceName } from "./marketplaceNames";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Spinner } from "./ui/spinner";
import { toastManager } from "./ui/toast";

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

function SaleNotice({ sale }: { sale: MarketplaceSale }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>(sale.listedElsewhere);
  const soldOn = marketplaceName(sale.marketplace);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["marketplace-sales"] });

  const delist = useMutation({
    mutationFn: (marketplaces: string[]) => api.marketplaceSales.delist(sale.id, marketplaces),
    onSuccess: (_reply, marketplaces) => {
      toastManager.add({
        type: "success",
        title: "Delist sent to Vendoo",
        description: `Vendoo is taking it down from ${listJoin(marketplaces.map(marketplaceName))}. Check Vendoo if any marketplace still shows it.`,
      });
      refresh();
    },
    onError: (err: Error) => {
      toastManager.add({ type: "error", title: "Could not delist through Vendoo", description: err.message || "Is Chrome connected?" });
    },
  });
  const dismiss = useMutation({ mutationFn: () => api.marketplaceSales.dismiss(sale.id), onSuccess: refresh });

  async function confirmDelist() {
    const names = listJoin(selected.map(marketplaceName));
    const confirmed = await confirmDialog(
      `Delist from ${names}?\nIt sold on ${soldOn}. Vendoo will take it down from ${names}. ${soldOn} handles its own listing.`,
      { variant: "destructive", confirmLabel: "Delist" },
    );
    if (confirmed) delist.mutate(selected);
  }

  const busy = delist.isPending || dismiss.isPending;
  const toggle = (marketplace: string, on: boolean) =>
    setSelected((current) => (on ? [...current, marketplace] : current.filter((m) => m !== marketplace)));

  return (
    <Alert variant="success" role="status" controlAlignment="first-line" className="mx-3 mt-2.5 shrink-0">
      <BadgeDollarSignIcon />
      <AlertTitle>
        Sold on {soldOn}
        {sale.quantity && sale.quantity > 1 ? ` (${sale.quantity})` : ""}
      </AlertTitle>
      <AlertDescription className="gap-2">
        <p>
          {soldWhen(sale) ? `${soldOn} reported this sale ${soldWhen(sale)}. ` : null}
          {sale.listedElsewhere.length
            ? "Vendoo still shows it listed on the marketplaces below. Choose which to delist."
            : "Vendoo doesn't show it listed anywhere else."}
        </p>
        {sale.listedElsewhere.length ? (
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {sale.listedElsewhere.map((marketplace) => (
              <label key={marketplace} className="flex items-center gap-1.5 text-xs text-foreground">
                <Checkbox
                  checked={selected.includes(marketplace)}
                  disabled={busy}
                  onCheckedChange={(checked) => toggle(marketplace, Boolean(checked))}
                />
                {marketplaceName(marketplace)}
              </label>
            ))}
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {sale.listedElsewhere.length ? (
            <Button size="xs" disabled={busy || selected.length === 0} onClick={() => void confirmDelist()}>
              {delist.isPending ? <Spinner /> : null}
              {selected.length ? `Delist from ${selected.length}` : "Delist"}
            </Button>
          ) : null}
          <Button size="xs" variant="ghost" disabled={busy} onClick={() => dismiss.mutate()}>
            {sale.listedElsewhere.length ? "I'll handle it" : "Dismiss"}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

/** Sales eBay or Etsy reported for this listing, waiting on the seller. */
export function SoldElsewhereNotice({ convId }: { convId: string }) {
  const { data } = useQuery({
    queryKey: ["marketplace-sales", convId],
    queryFn: () => api.marketplaceSales.list({ conversationId: convId }),
    refetchInterval: SALES_POLL_MS,
  });
  const sales = data?.sales ?? [];
  if (!sales.length) return null;
  return (
    <>
      {sales.map((sale) => (
        <SaleNotice key={sale.id} sale={sale} />
      ))}
    </>
  );
}
