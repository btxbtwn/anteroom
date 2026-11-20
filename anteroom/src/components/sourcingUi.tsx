/** Building blocks shared by the Sourcing tabs (boxes, scout) and BoughtBoxes. */
import type { ReactNode } from "react";
import { ChevronRightIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { formatMoney } from "./analyticsFormat";
import { Checkbox } from "./ui/checkbox";
import { Panel } from "./WorkspacePage";

/** An inline label beside its control, as in the settings strip. */
export function SourcingSetting({ label, children, className }: { label: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-2 text-xs text-muted-foreground", className)}>
      {label}
      {children}
    </span>
  );
}

/** A checkbox with its label, for the measured-from-your-sales switches. */
export function SourcingCheck({ checked, disabled, onChange, children }: {
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
  children: ReactNode;
}) {
  return (
    <label className={cn("inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground", disabled && "cursor-not-allowed opacity-64")}>
      <Checkbox checked={checked} disabled={disabled} onCheckedChange={(next) => onChange(next === true)} />
      {children}
    </label>
  );
}

/** A collapsible block of supporting detail. */
export function SourcingDetails({ summary, open, onToggle, className, children }: {
  summary: ReactNode;
  open?: boolean;
  onToggle?: (open: boolean) => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <details
      className={cn("group/details flex flex-col text-xs", className)}
      open={open}
      onToggle={onToggle ? (event) => onToggle(event.currentTarget.open) : undefined}
    >
      <summary className="inline-flex w-fit cursor-pointer list-none items-center gap-1 rounded-md font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 transition-transform group-open/details:rotate-90" />
        {summary}
      </summary>
      <div className="mt-2 flex flex-col gap-2">{children}</div>
    </details>
  );
}

/** A box or check: what it is on the left, the money on the right. */
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
