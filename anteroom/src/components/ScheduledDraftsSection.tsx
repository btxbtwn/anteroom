/**
 * Scheduled Draft Publishing section on AutomationsPage.
 *
 * Seller opt-in only: drafts must be marked "Ready to list" and explicitly
 * scheduled for named marketplaces at a future date/time. Supports single and
 * bulk schedule/unschedule, failure alerts, and audit log history.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClockIcon, ChevronDownIcon } from "lucide-react";

import { api } from "../api/client";
import { scheduledDraftsQuery } from "../api/pageQueries";
import type { DraftScheduleState } from "../api/automations";
import { confirmDialog } from "../ui/confirmDialog";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from "./ui/collapsible";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";
import { EmptyRow, PageError, PageSection, Panel, RowList } from "./WorkspacePage";

const SCHEDULE_MARKETPLACES = ["ebay", "etsy", "poshmark", "mercari", "depop"] as const;
const KEY = scheduledDraftsQuery.queryKey;
const LOGS_KEY = ["scheduled-draft-logs"] as const;

function defaultFutureInput(): string {
  const next = new Date(Date.now() + 60 * 60_000);
  next.setSeconds(0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}T${pad(next.getHours())}:${pad(next.getMinutes())}`;
}

function formatWhen(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "Not scheduled";
}

function statusSummary(draft: DraftScheduleState): string {
  if (draft.scheduled_status === "failed") {
    return `Failed: ${draft.scheduled_error || "Marketplace did not confirm publish."}`;
  }
  if (draft.scheduled_publish_at && draft.scheduled_marketplaces.length) {
    return `Scheduled for ${formatWhen(draft.scheduled_publish_at)} on ${draft.scheduled_marketplaces.map(marketplaceName).join(", ")}`;
  }
  if (draft.ready_to_list) {
    return "Ready to list · not scheduled yet";
  }
  return "Draft";
}

export function ScheduledDraftsSection({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const queryClient = useQueryClient();
  const draftsQuery = useQuery(scheduledDraftsQuery);
  const logsQuery = useQuery({
    queryKey: LOGS_KEY,
    queryFn: () => api.automations.scheduledDraftLogs(30),
  });
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [whenInput, setWhenInput] = useState(defaultFutureInput);
  const [markets, setMarkets] = useState<string[]>(["poshmark"]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: KEY });
    void queryClient.invalidateQueries({ queryKey: LOGS_KEY });
  };

  const bulkSchedule = useMutation({
    mutationFn: ({ ids, at, targets }: { ids: string[]; at: string; targets: string[] }) =>
      api.automations.bulkScheduleDrafts(ids, at, targets),
    onSuccess: (res) => {
      const okCount = res.scheduled?.length ?? 0;
      const errCount = res.failed.length;
      setNotice(
        errCount
          ? `Scheduled ${okCount} ${okCount === 1 ? "draft" : "drafts"}; ${errCount} could not be scheduled.`
          : `Scheduled ${okCount} ${okCount === 1 ? "draft" : "drafts"}.`,
      );
      if (errCount) {
        setError(res.failed.map((e) => e.error).join(" "));
      } else {
        setError("");
      }
      setSelected({});
      refresh();
    },
    onError: (err: Error) => setError(err.message),
  });

  const bulkUnschedule = useMutation({
    mutationFn: (ids: string[]) => api.automations.bulkUnscheduleDrafts(ids),
    onSuccess: (res) => {
      const count = res.unscheduled?.length ?? 0;
      setNotice(`Unscheduled ${count} ${count === 1 ? "draft" : "drafts"}.`);
      setError("");
      setSelected({});
      refresh();
    },
    onError: (err: Error) => setError(err.message),
  });

  const unscheduleOne = useMutation({
    mutationFn: (convId: string) => api.draftSchedule.unschedule(convId),
    onSuccess: () => {
      setNotice("Draft unscheduled.");
      setError("");
      refresh();
    },
    onError: (err: Error) => setError(err.message),
  });

  const drafts = draftsQuery.data ?? [];
  const logs = logsQuery.data ?? [];
  const selectedIds = drafts.filter((d) => selected[d.conversation_id]).map((d) => d.conversation_id);
  const busy = bulkSchedule.isPending || bulkUnschedule.isPending || unscheduleOne.isPending;
  const failedDrafts = drafts.filter((d) => d.scheduled_status === "failed");

  const toggleMarket = (market: string, checked: boolean) => {
    setMarkets((prev) => (checked ? [...new Set([...prev, market])] : prev.filter((m) => m !== market)));
  };

  const handleBulkSchedule = async () => {
    if (!selectedIds.length) return;
    if (!markets.length) {
      setError("Pick at least one marketplace before scheduling.");
      return;
    }
    const parsed = new Date(whenInput);
    if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
      setError("Choose a future date and time.");
      return;
    }
    const marketLabels = markets.map(marketplaceName).join(", ");
    const ok = await confirmDialog(
      `Schedule ${selectedIds.length} ${selectedIds.length === 1 ? "draft" : "drafts"} to publish on ${marketLabels} at ${parsed.toLocaleString()}? Editing a scheduled draft later will unschedule it automatically.`,
      { confirmLabel: "Schedule publish" },
    );
    if (!ok) return;
    bulkSchedule.mutate({ ids: selectedIds, at: parsed.toISOString(), targets: markets });
  };

  return (
    <PageSection
      title="Scheduled draft publishing"
      description="Opt-in publishing for drafts marked Ready to list. Drafts publish only to the marketplaces and time you explicitly schedule."
    >
      {error ? <PageError message={error} /> : null}
      {notice ? <p role="status" className="text-xs text-muted-foreground">{notice}</p> : null}
      {failedDrafts.length ? (
        <Panel divided={false} className="border-destructive/40 px-4 py-3 text-xs text-destructive-foreground">
          {failedDrafts.length} scheduled {failedDrafts.length === 1 ? "draft" : "drafts"} failed to publish. Review and reschedule below.
        </Panel>
      ) : null}

      {draftsQuery.isLoading ? (
        <Panel divided={false} className="px-4 py-3 text-xs text-muted-foreground">
          <Spinner size="sm" /> Loading scheduled drafts…
        </Panel>
      ) : !drafts.length ? (
        <EmptyRow>
          No drafts marked Ready to list yet. Open a draft and toggle Ready to list under Marketplaces to schedule it.
        </EmptyRow>
      ) : (
        <Panel divided={false}>
          <div className="flex flex-wrap items-end gap-3 border-b border-border/50 px-4 py-3 text-xs">
            <label className="flex flex-col gap-1">
              Publish date and time
              <Input
                type="datetime-local"
                className="w-52"
                aria-label="Bulk schedule publish date and time"
                value={whenInput}
                disabled={busy}
                onChange={(event) => setWhenInput(event.target.value)}
              />
            </label>
            <fieldset className="flex flex-col gap-1">
              <legend className="text-muted-foreground">Marketplaces</legend>
              <div className="flex flex-wrap items-center gap-3 pt-1">
                {SCHEDULE_MARKETPLACES.map((market) => (
                  <label key={market} className="inline-flex cursor-pointer items-center gap-1.5 text-foreground">
                    <Checkbox
                      checked={markets.includes(market)}
                      disabled={busy}
                      onCheckedChange={(checked) => toggleMarket(market, checked === true)}
                    />
                    {marketplaceName(market)}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="xs" disabled={busy || !selectedIds.length} onClick={() => void handleBulkSchedule()}>
                {bulkSchedule.isPending ? <Spinner size="sm" /> : <CalendarClockIcon className="size-3.5" />}
                Schedule selected ({selectedIds.length})
              </Button>
              <Button
                size="xs"
                variant="outline"
                disabled={busy || !selectedIds.length}
                onClick={() => bulkUnschedule.mutate(selectedIds)}
              >
                Unschedule selected
              </Button>
            </div>
          </div>

          <RowList>
            {drafts.map((draft) => (
              <li key={draft.conversation_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 text-sm">
                <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2.5">
                  <Checkbox
                    checked={Boolean(selected[draft.conversation_id])}
                    disabled={busy}
                    onCheckedChange={(checked) =>
                      setSelected((prev) => ({ ...prev, [draft.conversation_id]: checked === true }))
                    }
                  />
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="truncate font-medium text-foreground">{draft.title || draft.conversation_id}</span>
                    <span
                      className={
                        draft.scheduled_status === "failed"
                          ? "truncate text-xs text-destructive-foreground"
                          : "truncate text-xs text-muted-foreground"
                      }
                    >
                      {statusSummary(draft)}
                    </span>
                  </span>
                </label>
                <div className="flex items-center gap-2">
                  <Button size="xs" variant="ghost" onClick={() => onOpenListing(draft.conversation_id)}>
                    Open draft
                  </Button>
                  {draft.scheduled_publish_at ? (
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={busy}
                      onClick={() => unscheduleOne.mutate(draft.conversation_id)}
                    >
                      Unschedule
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </RowList>
        </Panel>
      )}

      {logs.length ? (
        <Collapsible>
          <CollapsibleTrigger className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
            <ChevronDownIcon className="size-3.5" />
            Scheduled publish history ({logs.length})
          </CollapsibleTrigger>
          <CollapsiblePanel className="mt-2">
            <Panel>
              {logs.map((entry) => (
                <div key={entry.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-xs">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-foreground">
                      {entry.title || entry.conversation_id} · {entry.marketplaces.map(marketplaceName).join(", ")}
                    </p>
                    <p className={entry.status === "failed" ? "text-destructive-foreground" : "text-muted-foreground"}>
                      {entry.status}
                      {entry.error ? ` · ${entry.error}` : ""}
                    </p>
                  </div>
                  <span className="tabular-nums text-muted-foreground">{formatWhen(entry.created_at)}</span>
                </div>
              ))}
            </Panel>
          </CollapsiblePanel>
        </Collapsible>
      ) : null}
    </PageSection>
  );
}
