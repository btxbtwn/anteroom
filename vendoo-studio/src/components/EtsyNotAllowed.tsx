import { useQuery } from "@tanstack/react-query";
import { ChevronRightIcon } from "lucide-react";
import { api } from "../api/client";
import type { EtsyNotAllowedItem } from "../api/types";
import { formatDays } from "./analyticsFormat";
import { EmptyRow, PageError, PageSection, Panel, rowButtonClassName } from "./WorkspacePage";

const DAY_MS = 24 * 60 * 60 * 1000;

/** The muted line under a row: what Etsy is told, how long it has been up, and the vintage hint. */
export function etsyNotAllowedMeta(item: EtsyNotAllowedItem, now = Date.now()): string {
  const parts = [item.when_made ? `When made: ${item.when_made}` : "No When Made"];
  const listed = item.listed_at ? Date.parse(item.listed_at) : NaN;
  if (!Number.isNaN(listed)) parts.push(`on Etsy ${formatDays(Math.max(0, (now - listed) / DAY_MS))}`);
  if (item.title_says_vintage) parts.push("Title says vintage: if it is 20+ years old, fix When Made instead");
  return parts.join(" · ");
}

export function etsyNotAllowedSummary(count: number): string {
  return count === 1 ? "1 listing to delist on Etsy" : `${count} listings to delist on Etsy`;
}

/**
 * Items live on Etsy that Etsy removes as modern resale. Read-only: Anteroom
 * never delists. The seller opens each on Etsy and takes it down there.
 */
export function EtsyNotAllowed({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const query = useQuery({ queryKey: ["etsy-not-allowed"], queryFn: api.etsy.notAllowed });
  const items = query.data?.items || [];
  return (
    <PageSection
      aria-label="Not allowed on Etsy"
      title="Not allowed on Etsy"
      description={<>
        Live on Etsy, but modern resale, which Etsy removes under its Creativity Standards. Repeat removals put Star Seller
        at risk. Delist these on Etsy yourself; Anteroom never delists. Based on Anteroom's last Vendoo sync.
      </>}
    >
      {query.isError ? <PageError message={(query.error as Error).message || "Could not load Etsy listings."} /> : null}
      {query.isSuccess && items.length === 0 ? <EmptyRow>Everything live on Etsy is vintage, handmade, a supply, or digital.</EmptyRow> : null}
      {items.length > 0 ? (
        <Panel>
          <details className="group/etsy">
            <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-2.5 text-sm outline-none transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
              <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground transition-transform group-open/etsy:rotate-90" />
              <span className="min-w-0 flex-1 truncate text-foreground">{etsyNotAllowedSummary(items.length)}</span>
            </summary>
            <ul className="divide-y divide-border/50 border-t border-border/50 bg-background/40">
              {items.map((item) => (
                <li key={item.conversation_id} className="flex min-w-0 items-center">
                  <button type="button" className={rowButtonClassName} onClick={() => onOpenListing(item.conversation_id)}>
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate text-foreground">{item.title}</span>
                      <span className="truncate text-xs text-muted-foreground">{etsyNotAllowedMeta(item)}</span>
                    </span>
                  </button>
                  {item.etsy_url ? (
                    <a
                      className="shrink-0 px-4 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                      href={item.etsy_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open on Etsy
                    </a>
                  ) : null}
                </li>
              ))}
            </ul>
          </details>
        </Panel>
      ) : null}
    </PageSection>
  );
}
