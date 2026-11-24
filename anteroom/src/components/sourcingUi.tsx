/** Building blocks shared by BoughtBoxes. */
import type { ReactNode } from "react";
import { cn } from "../lib/utils";
import { formatMoney } from "./analyticsFormat";
import { Panel } from "./WorkspacePage";

/** An inline label beside its control, as in the box figures strip. */
export function SourcingSetting({ label, children, className }: { label: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-2 text-xs text-muted-foreground", className)}>
      {label}
      {children}
    </span>
  );
}

/** A box row: what it is on the left, the money on the right. */
export function SourcingItem({ main, money }: { main: ReactNode; money: ReactNode }) {
  return (
    <li className="flex min-w-0 flex-col gap-3 px-4 py-3 @xl/page:flex-row @xl/page:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-1">{main}</div>
      <div className="flex shrink-0 flex-col gap-0.5 text-sm text-foreground tabular-nums @xl/page:items-end @xl/page:text-right">{money}</div>
    </li>
  );
}

export const sourcingTitleClassName = "w-fit max-w-full text-left text-sm font-medium text-foreground hover:underline max-[900px]:-my-3 max-[900px]:py-3";
export const sourcingReasonClassName = "text-xs leading-relaxed text-muted-foreground";

export function Profit({ value, suffix = "" }: { value: number; suffix?: string }) {
  return (
    <span className={value >= 0 ? "text-success-foreground" : "text-destructive-foreground"}>
      {value >= 0 ? "+" : "−"}{formatMoney(Math.abs(Math.round(value)))}{suffix}
    </span>
  );
}

export function SourcingList({ children }: { children: ReactNode }) {
  return (
    <Panel divided={false}>
      <ol className="divide-y divide-border/50">{children}</ol>
    </Panel>
  );
}
