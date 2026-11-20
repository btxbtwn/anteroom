import { useQueryClient } from "@tanstack/react-query";
import { ClockIcon, ListChecksIcon, XCircleIcon } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { api } from "../api/client";
import { isOpenJobStatus } from "../api/polling";
import type { Job } from "../api/types";
import { busyFromJobs, MarketplaceWaitlist, useWaitlist, type WaitingAction } from "../lib/marketplaceWaitlist";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Popover, PopoverPopup, PopoverTrigger } from "./ui/popover";
import { Spinner } from "./ui/spinner";
import { toastManager } from "./ui/toast";

/** The page's one waitlist. It sends only the action the seller confirmed on that item. */
export const waitlist = new MarketplaceWaitlist((action) => {
  if (action.kind === "delist") return api.marketplaceListings.delist(action.convId, [action.marketplace]);
  if (action.kind === "save_draft") return api.nativeMarketplaces.saveDraft(action.convId, action.marketplace, action.revisionId);
  if (action.kind === "update") return api.nativeMarketplaces.update(action.convId, action.marketplace, action.revisionId);
  return api.nativeMarketplaces.list(action.convId, action.marketplace, action.revisionId);
});

const VERB: Record<WaitingAction["kind"], string> = { list: "List", update: "Update", save_draft: "Save draft", delist: "Delist" };

const NATIVE_STEP = /^native_(ebay|etsy|poshmark|mercari|depop)_(.+)$/;

/** What a running job is doing, in the seller's words; null for work that is not a marketplace action. */
function runningLabel(step: string | null | undefined): { marketplace: string | null; label: string } | null {
  if (step === "native_delist") return { marketplace: null, label: "Delisting" };
  const match = NATIVE_STEP.exec(step ?? "");
  if (!match) return null;
  const [, marketplace, action] = match;
  const name = marketplaceName(marketplace);
  if (action === "save_draft") return { marketplace, label: `Saving draft on ${name}` };
  if (action === "update") return { marketplace, label: `Updating on ${name}` };
  if (action === "inspect") return { marketplace, label: `Reading ${name}'s form` };
  if (action === "check") return { marketplace, label: `Checking ${name}` };
  return { marketplace, label: `Listing on ${name}` };
}

/** One line per thing running, waiting or stopped. */
function ActivityRow({ icon, title, label, onOpen, action }: {
  icon: ReactNode; title: string; label: string; onOpen: () => void; action?: ReactNode;
}) {
  return (
    <li className="flex min-h-11 items-center gap-2 px-3 py-1.5">
      <span className="flex size-4 shrink-0 items-center justify-center">{icon}</span>
      <button type="button" onClick={onOpen}
        className="min-w-0 flex-1 rounded-sm text-start hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span className="block truncate font-medium text-foreground text-sm">{title || "Untitled item"}</span>
        <span className="block truncate text-muted-foreground text-xs">{label}</span>
      </button>
      {action}
    </li>
  );
}

/**
 * Marketplace work across every item, like a browser's downloads list: what is
 * running, what waits for its marketplace (with Cancel), and what stopped.
 * Hidden while there is nothing to show.
 */
export function MarketplaceActivity({ jobs, checkedAt, onOpen }: {
  /** The app's read of open jobs, and when it was read. */
  jobs: readonly Job[] | undefined; checkedAt: number; onOpen: (convId: string) => void;
}) {
  const queryClient = useQueryClient();
  const waiting = useWaitlist(waitlist);
  const open = (jobs ?? []).filter((job) => isOpenJobStatus(job.status));
  const running = open.flatMap((job) => {
    const label = runningLabel(job.current_step);
    return label ? [{ job, ...label }] : [];
  });

  // Every queue read says which marketplaces are free; the first in line on each starts.
  useEffect(() => {
    if (checkedAt) waitlist.setBusy(busyFromJobs((jobs ?? []).filter((job) => isOpenJobStatus(job.status))));
  }, [jobs, checkedAt]);

  useEffect(() => {
    waitlist.listener = {
      started: (action) => {
        void queryClient.invalidateQueries({ queryKey: ["marketplace-listings", action.convId] });
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        void queryClient.invalidateQueries({ queryKey: ["native-marketplaces", action.convId] });
        void queryClient.invalidateQueries({ queryKey: ["jobs", action.convId] });
        void queryClient.invalidateQueries({ queryKey: ["queue"] });
      },
      failed: (action) => toastManager.add({
        type: "error",
        title: `${VERB[action.kind]} on ${marketplaceName(action.marketplace)} didn't start`,
        description: `${action.title}: ${action.error}`,
      }),
    };
    return () => { waitlist.listener = {}; };
  }, [queryClient]);

  const queued = waiting.filter((action) => !action.error);
  const stopped = waiting.filter((action) => action.error);
  const count = running.length + queued.length + stopped.length;
  if (!count) return null;
  const summary = [running.length && `${running.length} running`, queued.length && `${queued.length} waiting`,
    stopped.length && `${stopped.length} stopped`].filter(Boolean).join(", ");

  return (
    <Popover>
      <PopoverTrigger render={<Button variant="ghost" size="sm" aria-label={`Marketplace activity: ${summary}`} />}>
        {running.length || queued.some((action) => action.starting) ? <Spinner size="sm" tone="muted" /> : <ListChecksIcon aria-hidden />}
        <span className="tabular-nums">{count}</span>
      </PopoverTrigger>
      <PopoverPopup align="end" width="md" padding="none">
        <div className="flex items-baseline justify-between gap-2 border-b px-3 py-2">
          <span className="font-medium text-foreground text-sm">Marketplace activity</span>
          <span className="text-muted-foreground text-xs" role="status">{summary}</span>
        </div>
        <ul className="flex max-h-96 flex-col divide-y overflow-y-auto">
          {running.map(({ job, marketplace, label }) => (
            <ActivityRow key={job.id} title={job.listing_title} label={`${label}…`} onOpen={() => onOpen(job.conversation_id)}
              icon={marketplace ? <MarketplaceLogo id={marketplace} label={marketplaceName(marketplace)} size={16} /> : <Spinner size="sm" tone="muted" />} />
          ))}
          {queued.map((action) => {
            const ahead = waitlist.ahead(action);
            const name = marketplaceName(action.marketplace);
            return (
              <ActivityRow key={action.id} title={action.title} onOpen={() => onOpen(action.convId)}
                icon={action.starting ? <Spinner size="sm" tone="muted" /> : <ClockIcon aria-hidden className="size-3.5 text-muted-foreground" />}
                label={action.starting ? action.kind === "delist" ? `Delisting from ${name}…` : `Starting ${VERB[action.kind].toLowerCase()} on ${name}…`
                  : `${VERB[action.kind]} waits for ${name}${ahead > 0 ? ` · ${ahead} ahead` : ""}`}
                action={<Button size="xs" variant="ghost" disabled={action.starting} onClick={() => waitlist.remove(action.id)}>Cancel</Button>} />
            );
          })}
          {stopped.map((action) => (
            <ActivityRow key={action.id} title={action.title} onOpen={() => onOpen(action.convId)}
              icon={<XCircleIcon aria-hidden className="size-3.5 text-destructive" />}
              label={`${marketplaceName(action.marketplace)}: ${action.error}`}
              action={<Button size="xs" variant="ghost" onClick={() => waitlist.remove(action.id)}>Dismiss</Button>} />
          ))}
        </ul>
        <p className="border-t px-3 py-2 text-muted-foreground text-xs">Waiting actions start in turn while Anteroom stays open.</p>
      </PopoverPopup>
    </Popover>
  );
}
