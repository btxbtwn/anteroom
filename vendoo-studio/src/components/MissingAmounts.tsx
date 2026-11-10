import type { ReactNode } from "react";
import { TriangleAlertIcon } from "lucide-react";
import { missingList, saleCount } from "./analyticsFormat";

/**
 * Said once per page: how many sales are missing amounts and which ones, from the
 * same counts on both Analytics tabs. Rows only carry a quiet marker.
 */
export function MissingAmountsNotice({ records, counts, scope, action }: {
  records: number;
  counts: { label: string; count: number }[];
  /** Where the count comes from when it differs from the figures above, e.g. "across all time". */
  scope?: string;
  action?: ReactNode;
}) {
  if (records === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-border/60 bg-card/40 px-4 py-3 text-sm">
      <TriangleAlertIcon aria-hidden className="size-4 shrink-0 text-warning-foreground" />
      <p className="min-w-0 flex-1 basis-56">
        <span className="font-medium text-foreground">{saleCount(records)} missing sale data{scope ? ` ${scope}` : ""}</span>
        <span className="block text-xs text-muted-foreground">{missingList(counts)}</span>
      </p>
      {action}
    </div>
  );
}
