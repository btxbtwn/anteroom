/**
 * 60-day Sale Detection Tracker panel in ConnectionsSettings.
 *
 * Shows every detected sale over the last 60 days with SKU, timestamp,
 * per-marketplace auto-delist / auto-adjust quantity / Mercari relist outcomes,
 * retry state, error details, and one-click retry.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCwIcon } from "lucide-react";

import { api } from "../../api/client";
import type { SaleSyncOutcome } from "../../api/directConnections";
import { pollMs } from "../../api/polling";
import { marketplaceName } from "../marketplaceNames";
import { Button } from "../ui/button";
import { Spinner } from "../ui/spinner";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

const TRACKER_KEY = ["sale-detection-tracker"] as const;

function actionLabel(outcome: SaleSyncOutcome): string {
  if (outcome.action === "delist") return "Auto-delist";
  if (outcome.action === "mercari_relist") return "Mercari single-qty relist";
  if (outcome.previousQuantity != null && outcome.newQuantity != null) {
    return `Qty ${outcome.previousQuantity} → ${outcome.newQuantity}`;
  }
  return outcome.newQuantity != null ? `Qty → ${outcome.newQuantity}` : "Quantity sync";
}

function statusToneClass(status: SaleSyncOutcome["status"]): string {
  if (status === "succeeded") return "text-success-foreground";
  if (status === "failed") return "text-destructive-foreground";
  return "text-muted-foreground";
}

export function SaleDetectionTracker() {
  const queryClient = useQueryClient();
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const tracker = useQuery({
    queryKey: TRACKER_KEY,
    queryFn: () => api.marketplaceSales.tracker(100),
    refetchInterval: () => pollMs(60_000),
  });

  const retry = useMutation({
    mutationFn: (eventId: string) => api.marketplaceSales.retryTrackerEvent(eventId),
    onMutate: (eventId) => {
      setRetryingId(eventId);
      setNotice("");
    },
    onSuccess: (res) => {
      setNotice(
        res.ok
          ? `${marketplaceName(res.outcome.marketplace)} synced.`
          : res.outcome.error || `${marketplaceName(res.outcome.marketplace)} still needs attention.`,
      );
      void queryClient.invalidateQueries({ queryKey: TRACKER_KEY });
    },
    onSettled: () => setRetryingId(null),
  });

  const data = tracker.data;
  const entries = data?.entries ?? [];

  return (
    <SettingsSection id="sale-tracker" title="60-day sale detection tracker">
      <SettingsRow
        title="Detected sales and cross-marketplace sync"
        description="Shows every sale detected over the last 60 days, its SKU, timestamp, auto-delist or quantity-adjust outcome on each marketplace, retry state, and any errors."
        control={
          <Button
            size="xs"
            variant="outline"
            disabled={tracker.isFetching}
            onClick={() => void tracker.refetch()}
          >
            {tracker.isFetching ? <Spinner size="sm" /> : <RefreshCwIcon className="size-3.5" />}
            Refresh
          </Button>
        }
      >
        {tracker.error ? <SettingsNote tone="error">{tracker.error.message}</SettingsNote> : null}
        {retry.error ? <SettingsNote tone="error">{retry.error.message}</SettingsNote> : null}
        {notice ? <SettingsNote>{notice}</SettingsNote> : null}

        {data ? (
          <p className="text-xs tabular-nums text-muted-foreground">
            {data.summary.totalSales} {data.summary.totalSales === 1 ? "sale" : "sales"} in the last {data.windowDays} days ·{" "}
            {data.summary.succeeded} synced · {data.summary.failed} failed · {data.summary.pending} pending
          </p>
        ) : null}

        {tracker.isLoading ? (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Spinner size="sm" tone="muted" />
            Loading 60-day tracker…
          </p>
        ) : !entries.length ? (
          <SettingsNote>No sales detected in the last 60 days.</SettingsNote>
        ) : (
          <div className="max-h-96 divide-y divide-border/50 overflow-y-auto rounded-lg border border-border/60">
            {entries.map((entry) => (
              <div key={entry.saleId} className="space-y-1.5 px-3 py-2.5 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <span className="font-medium text-foreground">{entry.title || `Order ${entry.orderId}`}</span>
                    {entry.sku ? (
                      <span className="ml-2 font-mono text-muted-foreground">SKU {entry.sku}</span>
                    ) : null}
                  </div>
                  <span className="tabular-nums text-muted-foreground">
                    {marketplaceName(entry.soldOn)} · {entry.soldAt ? new Date(entry.soldAt).toLocaleString() : ""}
                  </span>
                </div>
                <p className="text-muted-foreground">
                  Order status: {entry.orderStatus}
                  {entry.quantity != null ? ` · Qty sold: ${entry.quantity}` : ""}
                  {entry.remainingQuantity != null ? ` · Remaining: ${entry.remainingQuantity}` : ""}
                </p>
                {entry.outcomes.length ? (
                  <ul className="space-y-1 pt-0.5">
                    {entry.outcomes.map((outcome, index) => (
                      <li
                        key={outcome.eventId ?? `${entry.saleId}-${outcome.marketplace}-${index}`}
                        className="flex flex-wrap items-center justify-between gap-2 rounded border border-border/50 px-2.5 py-1.5"
                      >
                        <div className="min-w-0">
                          <span className="font-medium text-foreground">{marketplaceName(outcome.marketplace)}</span>
                          <span className="mx-1.5 text-muted-foreground">·</span>
                          <span className="text-muted-foreground">{actionLabel(outcome)}</span>
                          <span className="mx-1.5 text-muted-foreground">·</span>
                          <span className={statusToneClass(outcome.status)}>{outcome.status}</span>
                          {outcome.attempts > 0 ? (
                            <span className="ml-1.5 tabular-nums text-muted-foreground">
                              ({outcome.attempts} {outcome.attempts === 1 ? "try" : "tries"})
                            </span>
                          ) : null}
                          {outcome.newListingId ? (
                            <span className="ml-1.5 font-mono text-muted-foreground">New ID {outcome.newListingId}</span>
                          ) : null}
                          {outcome.error ? (
                            <p className="mt-0.5 text-destructive-foreground">{outcome.error}</p>
                          ) : null}
                          {outcome.nextRetryAt && outcome.status === "failed" ? (
                            <p className="mt-0.5 text-muted-foreground">
                              Next automatic retry: {new Date(outcome.nextRetryAt).toLocaleString()}
                            </p>
                          ) : null}
                        </div>
                        {outcome.retryable && outcome.eventId ? (
                          <Button
                            size="xs"
                            variant="outline"
                            disabled={retryingId === outcome.eventId}
                            onClick={() => retry.mutate(outcome.eventId!)}
                          >
                            {retryingId === outcome.eventId ? <Spinner size="sm" /> : null}
                            Retry
                          </Button>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-muted-foreground">No other marketplace actions recorded for this sale.</p>
                )}
              </div>
            ))}
          </div>
        )}
      </SettingsRow>
    </SettingsSection>
  );
}
