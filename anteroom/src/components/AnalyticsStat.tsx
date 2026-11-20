import type { ReactNode } from "react";
import { cn } from "../lib/utils";

/** One figure on the Analytics page: label, value, how it moved, then what it covers. */
export function Stat({
  label,
  value,
  hint,
  change,
  negative = false,
}: {
  label: string;
  /** null when it can't be worked out from what was recorded. */
  value: string | null;
  hint?: string;
  change?: string;
  negative?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-xl border border-border/60 bg-card/40 p-4 shadow-xs/5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={cn(
          "tabular-nums",
          value == null || value === "—" ? "text-sm text-muted-foreground" : "text-2xl font-semibold tracking-tight text-foreground",
          negative && "text-destructive-foreground",
        )}
      >
        {value ?? "Not enough data"}
      </div>
      {change ? <div className="text-xs font-medium text-foreground/80 tabular-nums">{change}</div> : null}
      {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

/** The grid the stat cards sit in. */
export function StatGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("grid grid-cols-1 gap-3 @lg/page:grid-cols-2 @3xl/page:grid-cols-3", className)}>{children}</div>;
}
