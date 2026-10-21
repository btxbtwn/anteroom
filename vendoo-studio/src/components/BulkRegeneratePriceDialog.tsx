import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { PriceDropPreview } from "../api/types";
import {
  BULK_DROP_PERCENTS,
  bulkPriceDrops,
  bulkRegenerateWarning,
  type BulkPriceChoice,
  type BulkPriceDrop,
  type BulkPriceSuggestion,
  type BulkRegenerateListing,
} from "../bulkRegenerate";
import { money, PriceChip, PriceDialog, PriceRow, PriceRows, PriceSection, PriceStatus } from "./PriceDropParts";
import { Button } from "./ui/button";

function suggestionFromPreview(preview: PriceDropPreview): BulkPriceSuggestion {
  const mode =
    preview.suggested_mode === "comps"
      ? "comps"
      : preview.suggested_mode === "percent" || preview.suggested_mode === "sell_through"
        ? "percent"
        : "custom";
  return {
    price: preview.suggested_price,
    percent: preview.suggested_effective_percent,
    mode,
  };
}

/**
 * Confirm a bulk rewrite, with the option to mark down every priced listing
 * first. Keep prices is the default, so a rewrite that only refreshes the
 * copy does not change what each listing asks.
 */
export function BulkRegeneratePriceDialog({
  listings,
  onClose,
  onConfirm,
}: {
  listings: BulkRegenerateListing[];
  onClose: () => void;
  onConfirm: (drops: Map<string, BulkPriceDrop>) => void;
}) {
  const [choice, setChoice] = useState<BulkPriceChoice>({ kind: "keep" });
  const [suggestions, setSuggestions] = useState<Map<string, BulkPriceSuggestion | null>>(new Map());
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const pricedIds = useMemo(
    () => listings.filter((listing) => listing.price != null && listing.price > 0).map((listing) => listing.id),
    [listings],
  );

  useEffect(() => {
    if (choice.kind !== "suggested" || pricedIds.length === 0) return;
    let cancelled = false;
    void Promise.all(
      pricedIds.map(async (id) => {
        try {
          const preview = await api.listings.priceDropPreview(id);
          return [id, suggestionFromPreview(preview)] as const;
        } catch {
          return [id, null] as const;
        }
      }),
    ).then((results) => {
      if (cancelled) return;
      const next = new Map<string, BulkPriceSuggestion | null>();
      let failed = 0;
      for (const [id, suggestion] of results) {
        next.set(id, suggestion);
        if (!suggestion) failed += 1;
      }
      setSuggestions(next);
      setLoadError(
        failed
          ? `${failed} ${failed === 1 ? "listing" : "listings"} could not load a suggestion and will keep ${failed === 1 ? "its" : "their"} price.`
          : "",
      );
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [choice.kind, pricedIds]);

  const drops = useMemo(
    () => bulkPriceDrops(listings, choice, suggestions),
    [choice, listings, suggestions],
  );
  const warning = bulkRegenerateWarning(listings.length);
  const [title, description] = warning.split("\n");
  const dropping = drops.size > 0;

  return (
    <PriceDialog
      title={title}
      description={`${description} Choose a markdown to drop every priced listing before the rewrite, or keep prices.`}
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={choice.kind === "suggested" && loading}
            onClick={() => onConfirm(drops)}
          >
            {dropping ? "Drop and rewrite" : "Rewrite"}
          </Button>
        </>
      }
    >
      <PriceSection title="Price">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Price drop">
          <PriceChip active={choice.kind === "keep"} onClick={() => setChoice({ kind: "keep" })}>
            Keep prices
          </PriceChip>
          {BULK_DROP_PERCENTS.map((percent) => (
            <PriceChip
              key={percent}
              active={choice.kind === "percent" && choice.percent === percent}
              onClick={() => setChoice({ kind: "percent", percent })}
            >
              −{percent}% each
            </PriceChip>
          ))}
          <PriceChip
            wide
            active={choice.kind === "suggested"}
            note="The same suggestion Regenerate offers on one listing, from its own sales and prior drops."
            onClick={() => {
              if (choice.kind === "suggested") return;
              setChoice({ kind: "suggested" });
              setLoadError("");
              setLoading(pricedIds.length > 0);
            }}
          >
            Each listing's suggestion
          </PriceChip>
        </div>
      </PriceSection>

      {choice.kind === "suggested" && loadError ? <PriceStatus error>{loadError}</PriceStatus> : null}
      {choice.kind === "suggested" && loading ? (
        <PriceStatus>Loading a suggestion for each priced listing…</PriceStatus>
      ) : null}

      <PriceRows>
        {listings.map((listing) => (
          <PriceRow key={listing.id} title={listing.title}>
            {rowPrice(listing, choice, drops, loading)}
          </PriceRow>
        ))}
      </PriceRows>
    </PriceDialog>
  );
}

function rowPrice(
  listing: BulkRegenerateListing,
  choice: BulkPriceChoice,
  drops: Map<string, BulkPriceDrop>,
  loading: boolean,
): string {
  const current = listing.price;
  if (current == null || !(current > 0)) return "No price";
  if (choice.kind === "suggested" && loading) return `${money(current)} → …`;
  const drop = drops.get(listing.id);
  if (!drop) return `${money(current)} · kept`;
  return `${money(current)} → ${money(drop.price)}`;
}
