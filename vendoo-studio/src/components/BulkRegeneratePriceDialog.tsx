import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { BULK_READ_CONCURRENCY, mapWithConcurrency } from "../lib/concurrency";
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
    expected_revision_id: preview.revision_id,
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
  const [loading, setLoading] = useState(true);
  const [previews, setPreviews] = useState<Map<string, PriceDropPreview>>(new Map());
  const [loadError, setLoadError] = useState("");
  const reviewIds = useMemo(
    () => listings.map((listing) => listing.id),
    [listings],
  );

  useEffect(() => {
    let cancelled = false;
    void mapWithConcurrency(reviewIds, BULK_READ_CONCURRENCY, async (id) => {
      try {
        const preview = await api.listings.priceDropPreview(id);
        return [id, preview] as const;
      } catch {
        return [id, null] as const;
      }
    }).then((results) => {
      if (cancelled) return;
      const next = new Map<string, BulkPriceSuggestion | null>();
      const reviewed = new Map<string, PriceDropPreview>();
      let failed = 0;
      for (const [id, preview] of results) {
        next.set(id, preview ? suggestionFromPreview(preview) : null);
        if (preview) reviewed.set(id, preview);
        else failed += 1;
      }
      setPreviews(reviewed);
      setSuggestions(next);
      setLoadError(
        failed
          ? `${failed} ${failed === 1 ? "listing" : "listings"} could not load a current price and will keep ${failed === 1 ? "its" : "their"} price.`
          : "",
      );
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [reviewIds]);

  const reviewedListings = useMemo(() => listings.map((listing) => {
    const preview = previews.get(listing.id);
    return { ...listing, price: preview?.current_price ?? listing.price, revision_id: preview?.revision_id };
  }), [listings, previews]);

  const drops = useMemo(
    () => bulkPriceDrops(reviewedListings, choice, suggestions),
    [choice, reviewedListings, suggestions],
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
            disabled={choice.kind !== "keep" && loading}
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
            onClick={() => setChoice({ kind: "suggested" })}
          >
            Each listing's suggestion
          </PriceChip>
        </div>
      </PriceSection>

      {choice.kind !== "keep" && loadError ? <PriceStatus error>{loadError}</PriceStatus> : null}
      {choice.kind !== "keep" && loading ? (
        <PriceStatus>Loading current prices and suggestions…</PriceStatus>
      ) : null}

      <PriceRows>
        {reviewedListings.map((listing) => (
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
  if (choice.kind !== "keep" && loading) return `${money(current)} → …`;
  const drop = drops.get(listing.id);
  if (!drop) return `${money(current)} · kept`;
  return `${money(current)} → ${money(drop.price)}`;
}
