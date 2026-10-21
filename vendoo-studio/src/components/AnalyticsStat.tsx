import type { ReactNode } from "react";
import { cn } from "../lib/utils";

/** One figure on the Analytics page, with what it covers and how it moved. */
export function Stat({
  label,
  value,
  hint,
  change,
  negative = false,
}: {
  label: string;
  value: string;
  hint?: string;
  change?: string;
  negative?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-xl border border-border/60 bg-card/40 p-4 shadow-xs/5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={cn(
          "text-2xl font-semibold tracking-tight text-foreground tabular-nums",
          negative && "text-destructive-foreground",
        )}
      >
        {value}
      </div>
      {hint ? <div className="text-xs leading-relaxed text-muted-foreground">{hint}</div> : null}
      {change ? <div className="mt-auto pt-1 text-xs font-medium text-foreground/80">{change}</div> : null}
    </div>
  );
}

/** The grid the stat cards sit in. */
export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-3 @lg/page:grid-cols-2 @3xl/page:grid-cols-3">{children}</div>;
}
