/**
 * Per-draft "Ready to list" toggle and future publish scheduler, opened from
 * the listing's actions. It is offered only on a draft that is not live (or
 * one already scheduled, so it can be unscheduled).
 *
 * Editing a scheduled draft automatically unschedules it so stale revisions
 * are never published unexpectedly; this control surfaces that warning clearly.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClockIcon } from "lucide-react";

import { api } from "../api/client";
import { refreshInventory } from "../api/inventoryRefresh";
import { scheduledDraftsQuery } from "../api/pageQueries";
import { confirmDialog } from "../ui/confirmDialog";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Dialog, DialogDescription, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";
import { Switch } from "./ui/switch";

const MARKETS = ["ebay", "etsy", "poshmark", "mercari", "depop"] as const;

function defaultFuture(): string {
  const next = new Date(Date.now() + 60 * 60_000);
  next.setSeconds(0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}T${pad(next.getHours())}:${pad(next.getMinutes())}`;
}

function useDraftSchedule(convId: string) {
  return useQuery({
    queryKey: ["draft-schedule", convId],
    queryFn: () => api.draftSchedule.get(convId),
  });
}

/** "Scheduled · Dec 1, 9:00 AM": short enough for a toolbar button. */
function shortWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/**
 * The listing's scheduling entry point. `draft` is false once the item is live
 * or sold anywhere; a schedule already set still shows, so it can be cancelled.
 * `render` draws the trigger, so the same dialog opens from a toolbar button
 * on a desktop and from the listing's menu on a phone.
 */
export function useDraftScheduleEntry(convId: string, draft: boolean) {
  const [open, setOpen] = useState(false);
  const state = useDraftSchedule(convId).data;
  const scheduledAt = state?.scheduled_publish_at ?? null;
  const available = typeof state?.ready_to_list === "boolean" && (draft || Boolean(scheduledAt));
  const label = scheduledAt ? `Scheduled · ${shortWhen(scheduledAt)}` : "Schedule…";
  const dialog = (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogPopup className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Schedule publishing</DialogTitle>
          <DialogDescription>Publish this draft later, on the marketplaces you pick.</DialogDescription>
        </DialogHeader>
        <DialogPanel>
          <DraftScheduleControl convId={convId} />
        </DialogPanel>
      </DialogPopup>
    </Dialog>
  );
  return { available, scheduled: Boolean(scheduledAt), label, open: () => setOpen(true), dialog };
}

function DraftScheduleControl({ convId }: { convId: string }) {
  const queryClient = useQueryClient();
  const scheduleQuery = useDraftSchedule(convId);
  const [whenInput, setWhenInput] = useState(defaultFuture);
  const [markets, setMarkets] = useState<string[]>(["poshmark"]);
  const [error, setError] = useState("");

  const refresh = () => {
    refreshInventory(queryClient);
    void queryClient.invalidateQueries({ queryKey: ["draft-schedule", convId] });
    void queryClient.invalidateQueries({ queryKey: scheduledDraftsQuery.queryKey });
  };

  const setReady = useMutation({
    mutationFn: (ready: boolean) => api.draftSchedule.setReady(convId, ready),
    onSuccess: () => { setError(""); refresh(); },
    onError: (err: Error) => setError(err.message),
  });

  const schedule = useMutation({
    mutationFn: ({ at, targets }: { at: string; targets: string[] }) =>
      api.draftSchedule.schedule(convId, at, targets),
    onSuccess: () => { setError(""); refresh(); },
    onError: (err: Error) => setError(err.message),
  });

  const unschedule = useMutation({
    mutationFn: () => api.draftSchedule.unschedule(convId),
    onSuccess: () => { setError(""); refresh(); },
    onError: (err: Error) => setError(err.message),
  });

  const state = scheduleQuery.data;
  const busy = setReady.isPending || schedule.isPending || unschedule.isPending;

  const handleSchedule = async () => {
    if (!markets.length) {
      setError("Choose at least one marketplace.");
      return;
    }
    const parsed = new Date(whenInput);
    if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
      setError("Choose a future publish date and time.");
      return;
    }
    const ok = await confirmDialog(
      `Schedule this draft to publish on ${markets.map(marketplaceName).join(", ")} at ${parsed.toLocaleString()}? If you edit this draft before then, Anteroom will unschedule it automatically.`,
      { confirmLabel: "Schedule publish" },
    );
    if (!ok) return;
    schedule.mutate({ at: parsed.toISOString(), targets: markets });
  };

  if (scheduleQuery.isLoading || !state || typeof state.ready_to_list !== "boolean") return null;

  return (
    <div className="space-y-3 text-sm">
      <label className="flex items-center justify-between gap-3">
        <span className="min-w-0">
          <span className="block font-medium text-foreground">Ready to list</span>
          <span className="block text-muted-foreground text-xs">Turn on once you've reviewed the draft.</span>
        </span>
        <Switch
          size="sm"
          checked={state.ready_to_list}
          disabled={busy}
          aria-label="Mark draft ready to list"
          onCheckedChange={(checked) => setReady.mutate(checked)}
        />
      </label>

      {error ? <p role="alert" className="text-destructive-foreground">{error}</p> : null}

      {state.scheduled_publish_at ? (
        <div className="space-y-2 border-t pt-3">
          <p className="font-medium text-foreground">
            Scheduled for {new Date(state.scheduled_publish_at).toLocaleString()} on{" "}
            {state.scheduled_marketplaces.map(marketplaceName).join(", ")}
          </p>
          <p className="text-muted-foreground text-xs">
            Editing this draft unschedules it, so unreviewed changes never go live.
          </p>
          {state.scheduled_status === "failed" && state.scheduled_error ? (
            <p className="text-destructive-foreground">Last publish error: {state.scheduled_error}</p>
          ) : null}
          <Button size="sm" variant="outline" disabled={busy} onClick={() => unschedule.mutate()}>
            {unschedule.isPending ? <Spinner size="sm" /> : null}
            Unschedule
          </Button>
        </div>
      ) : state.ready_to_list ? (
        <div className="space-y-3 border-t pt-3">
          <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <legend className="mb-2 font-medium text-muted-foreground text-xs">Marketplaces</legend>
            {MARKETS.map((market) => (
              <label key={market} className="inline-flex cursor-pointer items-center gap-1.5 text-foreground">
                <Checkbox
                  checked={markets.includes(market)}
                  disabled={busy}
                  onCheckedChange={(checked) =>
                    setMarkets((prev) => (checked === true ? [...new Set([...prev, market])] : prev.filter((m) => m !== market)))
                  }
                />
                {marketplaceName(market)}
              </label>
            ))}
          </fieldset>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              type="datetime-local"
              className="w-52"
              aria-label="Scheduled publish time"
              value={whenInput}
              disabled={busy}
              onChange={(e) => setWhenInput(e.target.value)}
            />
            <Button size="sm" disabled={busy} onClick={() => void handleSchedule()}>
              {schedule.isPending ? <Spinner size="sm" /> : <CalendarClockIcon className="size-3.5" />}
              Schedule publish
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
