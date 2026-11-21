import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { OfferLogEntry } from "../api/contract.gen";
import { marketplaceName } from "./marketplaceNames";
import { offerOutcome, offerPrice } from "./offerActivityFormat";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Sheet, SheetPopup, SheetTitle } from "./ui/sheet";
import { Skeleton } from "./ui/skeleton";

const SENT_AT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export function OfferLogRow({ entry }: { entry: OfferLogEntry }) {
  const problem = entry.status === "unconfirmed" || entry.status === "failed";
  return <li className="flex min-w-0 flex-col gap-0.5 px-4 py-2.5 text-sm">
    <span className="truncate text-foreground">{entry.title || entry.listing_id}</span>
    <span className="text-xs text-muted-foreground tabular-nums">
      {[marketplaceName(entry.marketplace), offerPrice(entry), SENT_AT.format(new Date(entry.sent_at))].filter(Boolean).join(" · ")}
    </span>
    <span className={`text-xs tabular-nums ${problem ? "text-warning-foreground" : "text-muted-foreground"}`}>{offerOutcome(entry)}</span>
  </li>;
}

/** Every offer Anteroom sent, on every marketplace, newest first. */
export function OfferLog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const log = useInfiniteQuery({
    queryKey: ["offer-log"],
    queryFn: ({ pageParam }) => api.analytics.offerLog(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_before,
    enabled: open,
    staleTime: 30_000,
  });
  const entries = log.data?.pages.flatMap((page) => page.entries) ?? [];
  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetPopup side="right" className="max-w-xl">
      <div className="flex h-14 shrink-0 items-center px-5">
        <SheetTitle className="text-base font-semibold">Offer log</SheetTitle>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 pb-5">
        <PageNote>Every offer Anteroom sent on eBay, Poshmark, Mercari and Depop, newest first.</PageNote>
        {log.error ? <PageError message={`Could not read the offer log: ${(log.error as Error).message}`} /> : null}
        {log.isLoading ? <Skeleton className="h-48" /> : null}
        {log.data && !entries.length ? <PageNote>No offers sent yet.</PageNote> : null}
        {entries.length ? <ul aria-label="Offers sent" className="rounded-xl border border-border/60 bg-card/40 [&>*+*]:border-t [&>*+*]:border-border/50">
          {entries.map((entry) => <OfferLogRow key={`${entry.marketplace}-${entry.id}`} entry={entry} />)}
        </ul> : null}
        {log.hasNextPage ? <Button variant="outline" size="sm" className="self-center" disabled={log.isFetchingNextPage}
          onClick={() => void log.fetchNextPage()}>{log.isFetchingNextPage ? "Loading…" : "Show older offers"}</Button> : null}
      </div>
    </SheetPopup>
  </Sheet>;
}
