import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PriceDropCompsSource, PriceDropPreview } from "../api/types";
import { toastManager } from "./ui/toast";
import { SoldCompsCard } from "./SoldCompsCard";
import { SearchIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { money, PriceChip, PriceDialog, PriceSection, PriceStatus } from "./PriceDropParts";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

/** "14.3" but "21" — a trailing .0 is noise on a chip. */
function percentLabel(percent: number): string {
  return Number.isInteger(percent) ? String(percent) : percent.toFixed(1);
}

function formatDropWhen(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const days = Math.max(0, Math.round((Date.now() - date.getTime()) / 86_400_000));
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  return date.toLocaleDateString();
}

type Selection =
  | { kind: "suggested"; price: number }
  | { kind: "percent"; percent: number; price: number }
  | { kind: "comps"; price: number }
  | { kind: "custom"; price: number };

/** The server weighs prior drops, their recency, and live sold comps; that
 *  answer is the default rather than a fixed percentage off. */
function defaultSelection(preview: PriceDropPreview): Selection {
  return { kind: "suggested", price: preview.suggested_price };
}

type CompsStream = {
  sources: PriceDropCompsSource[];
  step: string;
  preview: PriceDropPreview | null;
  running: boolean;
  error: string;
};

const IDLE_COMPS: CompsStream = { sources: [], step: "", preview: null, running: false, error: "" };

/** Sold comps stream in source by source; each update carries the preview they imply. */
function usePriceDropComps(convId: string, enabled: boolean): CompsStream {
  const [state, setState] = useState<CompsStream>(IDLE_COMPS);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    // A fresh search starts from nothing; the previous open's comps are stale.
    queueMicrotask(() => {
      if (!controller.signal.aborted) setState({ ...IDLE_COMPS, running: true });
    });
    api.listings
      .priceDropComps(
        convId,
        {
          onSource: (source) =>
            setState((prev) => ({
              ...prev,
              sources: [...prev.sources.filter((item) => item.source !== source.source), source],
            })),
          onStep: (step) => setState((prev) => ({ ...prev, step })),
          onPreview: (preview) => setState((prev) => ({ ...prev, preview })),
        },
        controller.signal,
      )
      .then(() => setState((prev) => ({ ...prev, running: false, step: "" })))
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setState((prev) => ({ ...prev, running: false, step: "", error: err.message || "Sold comps search failed." }));
      });
    return () => controller.abort();
  }, [convId, enabled]);
  return enabled ? state : IDLE_COMPS;
}

function sourceStatus(source: PriceDropCompsSource): string {
  if (source.state === "searching") return "searching…";
  if (source.state === "timeout") return "timed out";
  if (source.state === "failed") return source.detail ? `failed — ${source.detail}` : "failed";
  return `${source.sold} sold · ${source.live} live`;
}

/** Price-drop chooser shown when Regenerate is clicked on a priced listing. */
export function RegeneratePriceDialog({
  convId,
  open,
  onClose,
  onRewrite,
  onDropped,
}: {
  convId: string;
  open: boolean;
  onClose: () => void;
  onRewrite: () => void;
  onDropped?: () => void;
}) {
  const queryClient = useQueryClient();

  // History and sell-through answer at once; sold comps are a web search by
  // the listing model that can take minutes, so they run only when the seller
  // asks and stream in on top instead of holding the whole dialog.
  const previewQuery = useQuery({
    queryKey: ["price-drop-preview", convId],
    queryFn: () => api.listings.priceDropPreview(convId),
    enabled: open,
    staleTime: 30_000,
  });
  const [compsRequested, setCompsRequested] = useState(false);
  const comps = usePriceDropComps(
    convId,
    open && compsRequested && Boolean(previewQuery.data?.comps.available),
  );
  const preview = comps.preview ?? previewQuery.data;

  const [selection, setSelection] = useState<Selection | null>(null);
  const [customText, setCustomText] = useState("");
  // Once the seller picks a price, comps arriving must not move it.
  const [picked, setPicked] = useState(false);

  const choose = (next: Selection, text = String(next.price)) => {
    setSelection(next);
    setCustomText(text);
    setPicked(true);
  };

  useEffect(() => {
    if (!open) {
      setCompsRequested(false);
      setSelection(null);
      setCustomText("");
      setPicked(false);
      return;
    }
    if (!preview || picked) return;
    const next = defaultSelection(preview);
    setSelection(next);
    setCustomText(String(next.price));
  }, [open, preview, picked]);

  const otherCuts = useMemo(
    () =>
      (preview?.drop_options || []).filter((option) => option.price !== preview?.suggested_price),
    [preview],
  );

  const selectedPrice = useMemo(() => {
    if (!selection) return null;
    if (selection.kind === "custom") {
      const parsed = Number(customText);
      return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : null;
    }
    return selection.price;
  }, [selection, customText]);

  const apply = useMutation({
    mutationFn: async () => {
      if (selectedPrice == null || !preview) {
        throw new Error("Choose a price first.");
      }
      // Typing the current price back in is a rewrite at today's price, not an
      // error: there is nothing to record, so skip straight to the rewrite.
      if (selectedPrice >= preview.current_price) return null;
      const percent =
        selection?.kind === "percent"
          ? selection.percent
          : Math.round((1 - selectedPrice / preview.current_price) * 1000) / 10;
      const mode =
        selection?.kind === "comps"
          ? "comps"
          : selection?.kind === "percent"
            ? "percent"
            : selection?.kind === "suggested" && preview.suggested_mode === "comps"
              ? "comps"
              : selection?.kind === "suggested"
                ? "percent"
                : "custom";
      return api.listings.applyPriceDrop(convId, { price: selectedPrice, percent, mode });
    },
    onSuccess: (result) => {
      onClose();
      void queryClient.invalidateQueries({ queryKey: ["listing", convId] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["price-drop-preview", convId] });
      onDropped?.();
      if (result) {
        toastManager.add({
          type: "success",
          title: "Price dropped",
          description: `${money(result.previous_price)} → ${money(result.price)}. Rewriting the listing at the new price.`,
        });
      }
      // The rewrite reads the price off the listing the drop just saved.
      onRewrite();
    },
    onError: (err: Error) => {
      toastManager.add({
        type: "error",
        title: "Could not drop price",
        description: err.message || "The price could not be updated.",
      });
    },
  });

  if (!open) return null;

  const belowComps =
    preview?.comps.target_price != null &&
    selectedPrice != null &&
    selectedPrice < preview.comps.target_price;

  return (
    <PriceDialog
      title="Regenerate"
      description="Confirm drops the price and rewrites the listing from scratch at the new price. Chat and generated fields are discarded; measurements, flaws, COG, labels and notes are kept. Nothing changes on Vendoo until you Send."
      onClose={onClose}
      busy={apply.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={apply.isPending || !preview || selectedPrice == null}
            onClick={() => apply.mutate()}
          >
            {apply.isPending ? "Saving…" : "Confirm"}
          </Button>
        </>
      }
    >
      {previewQuery.isLoading ? (
        <PriceStatus>Loading price history…</PriceStatus>
      ) : previewQuery.isError ? (
        <PriceStatus error>
          {(previewQuery.error as Error).message || "Could not load a price suggestion."}
        </PriceStatus>
      ) : preview ? (
        <>
          <div className="grid grid-cols-3 divide-x overflow-hidden rounded-xl border bg-card">
            {([
              ["Current", preview.current_price],
              ["First listed", preview.first_price],
              ["Selected", selectedPrice],
            ] as const).map(([label, value]) => (
              <div key={label} className="flex flex-col gap-0.5 px-3 py-2">
                <span className="text-muted-foreground text-xs">{label}</span>
                <strong className="font-semibold text-base tabular-nums">{money(value)}</strong>
              </div>
            ))}
          </div>

          <PriceSection title={`Suggested${preview.suggested_mode === "sell_through" ? " · from your sales" : ""}`}>
            <PriceChip
              wide
              active={selection?.kind === "suggested"}
              note={preview.suggested_reason}
              onClick={() => choose({ kind: "suggested", price: preview.suggested_price })}
            >
              {money(preview.suggested_price)} · −{percentLabel(preview.suggested_effective_percent)}%
            </PriceChip>
          </PriceSection>

          {/* The suggested chip already tells this story when it is the source. */}
          {preview.sell_through && preview.suggested_mode !== "sell_through" ? (
            <p className="text-muted-foreground text-xs">
              {preview.sell_through.scope === "category"
                ? `Your ${preview.sell_through.label}`
                : preview.sell_through.scope === "brand"
                  ? `Your ${preview.sell_through.label} items`
                  : "Your sold items"}
              : {preview.sell_through.count} sold
              {preview.sell_through.median_discount_percent != null
                ? ` at a median ${percentLabel(preview.sell_through.median_discount_percent)}% off`
                : ""}
              {preview.sell_through.median_days != null
                ? `, typically in ${preview.sell_through.median_days} days`
                : ""}
              {preview.age_days != null ? ` · this one: ${preview.age_days} days listed` : ""}
            </p>
          ) : null}

          {otherCuts.length ? (
            <PriceSection title="Other cuts">
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Price drop options">
                {otherCuts.map((option) => (
                  <PriceChip
                    key={option.price}
                    active={selection?.kind === "percent" && selection.price === option.price}
                    onClick={() =>
                      choose({
                        kind: "percent",
                        percent: option.effective_percent,
                        price: option.price,
                      })
                    }
                  >
                    {money(option.price)} · −{percentLabel(option.effective_percent)}%
                  </PriceChip>
                ))}
              </div>
            </PriceSection>
          ) : null}

          {preview.comps.target_price != null && preview.comps.target_price < preview.current_price ? (
            <PriceSection title="Live comps">
              <PriceChip
                wide
                active={selection?.kind === "comps"}
                onClick={() => choose({ kind: "comps", price: preview.comps.target_price! })}
              >
                Comps target {money(preview.comps.target_price)} · −
                {percentLabel(
                  Math.round(
                    (1 - preview.comps.target_price / preview.current_price) * 1000,
                  ) / 10,
                )}
                %{preview.comps.market ? ` · market ${preview.comps.market}` : ""}
              </PriceChip>
            </PriceSection>
          ) : !preview.comps.available ? (
            <PriceStatus>
              Sold comps need ChatGPT, Cursor or MiMo connected, or a Brave Search API key in Settings.
            </PriceStatus>
          ) : comps.running ? (
            <PriceStatus>
              Researching sold comps… You can confirm now, or wait for the live comps target.
            </PriceStatus>
          ) : comps.error ? (
            <PriceStatus error>{comps.error}</PriceStatus>
          ) : !compsRequested ? (
            <PriceSection title="Live comps">
              <Button variant="outline" size="sm" className="w-fit" onClick={() => setCompsRequested(true)}>
                <SearchIcon />
                Search sold comps
              </Button>
            </PriceSection>
          ) : null}

          {comps.sources.length ? (
            <ul className="flex flex-col gap-1 rounded-lg border bg-card px-3 py-2 text-xs" aria-live="polite">
              {comps.sources.map((source) => (
                <li key={source.source} className="flex items-center justify-between gap-2">
                  <span className="font-medium">{source.source}</span>
                  <span
                    className={cn(
                      "text-muted-foreground",
                      source.state === "done" && "text-success-foreground",
                      (source.state === "failed" || source.state === "timeout") && "text-destructive-foreground",
                    )}
                  >
                    {sourceStatus(source)}
                  </span>
                </li>
              ))}
              {comps.step ? <li className="text-muted-foreground">{comps.step}</li> : null}
            </ul>
          ) : null}

          <PriceSection title="Custom price" htmlFor="price-drop-custom">
            <Input
              nativeInput
              id="price-drop-custom"
              className="max-w-40"
              type="number"
              min={1}
              step={1}
              value={customText}
              onChange={(event) =>
                choose(
                  { kind: "custom", price: Number(event.target.value) || 0 },
                  event.target.value,
                )
              }
            />
            {belowComps ? (
              <PriceStatus>
                Below the comps formula floor ({money(preview.comps.target_price)}). You can still apply it.
              </PriceStatus>
            ) : null}
          </PriceSection>

          {preview.history.length ? (
            <PriceSection title="Prior drops">
              <ul className="flex flex-col gap-0.5 text-muted-foreground text-xs tabular-nums">
                {preview.history.map((event) => (
                  <li key={event.revision_id}>
                    {money(event.from_price)} → {money(event.to_price)} (−{event.percent}%)
                    {event.created_at ? ` · ${formatDropWhen(event.created_at)}` : ""}
                  </li>
                ))}
              </ul>
            </PriceSection>
          ) : null}

          {preview.comps.text ? <SoldCompsCard text={preview.comps.text} /> : null}
        </>
      ) : null}
    </PriceDialog>
  );
}
