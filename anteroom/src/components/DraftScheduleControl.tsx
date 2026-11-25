/**
 * Per-draft "Ready to list" toggle and future publish scheduler.
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

export function DraftScheduleControl({ convId }: { convId: string }) {
  const queryClient = useQueryClient();
  const scheduleQuery = useQuery({
    queryKey: ["draft-schedule", convId],
    queryFn: () => api.draftSchedule.get(convId),
  });
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
    <div className="space-y-2.5 rounded-lg border border-border/60 p-3 text-xs">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium text-foreground">Ready to list &amp; schedule publish</p>
          <p className="text-muted-foreground">
            Mark validated drafts ready to schedule future publishing to specific marketplaces.
          </p>
        </div>
        <Switch
          size="sm"
          checked={state.ready_to_list}
          disabled={busy}
          aria-label="Mark draft ready to list"
          onCheckedChange={(checked) => setReady.mutate(checked)}
        />
      </div>

      {error ? <p role="alert" className="text-destructive-foreground">{error}</p> : null}

      {state.scheduled_publish_at ? (
        <div className="space-y-2 rounded-md border border-border/60 bg-muted/30 p-2.5">
          <p className="font-medium text-foreground">
            Scheduled for {new Date(state.scheduled_publish_at).toLocaleString()} on{" "}
            {state.scheduled_marketplaces.map(marketplaceName).join(", ")}
          </p>
          <p className="text-muted-foreground">
            Saving any edit to this draft will automatically unschedule it so unreviewed changes never go live.
          </p>
          {state.scheduled_status === "failed" && state.scheduled_error ? (
            <p className="text-destructive-foreground">Last publish error: {state.scheduled_error}</p>
          ) : null}
          <Button size="xs" variant="outline" disabled={busy} onClick={() => unschedule.mutate()}>
            {unschedule.isPending ? <Spinner size="sm" /> : null}
            Unschedule
          </Button>
        </div>
      ) : state.ready_to_list ? (
        <div className="space-y-2 pt-1">
          <div className="flex flex-wrap items-center gap-3">
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
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              type="datetime-local"
              className="w-52"
              aria-label="Scheduled publish time"
              value={whenInput}
              disabled={busy}
              onChange={(e) => setWhenInput(e.target.value)}
            />
            <Button size="xs" disabled={busy} onClick={() => void handleSchedule()}>
              {schedule.isPending ? <Spinner size="sm" /> : <CalendarClockIcon className="size-3.5" />}
              Schedule publish
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
