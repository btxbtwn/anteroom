/** Shared pieces of the price drop dialogs, on T3 Code's dialog and controls. */
import type { ReactNode } from "react";
import { cn } from "../lib/utils";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
} from "./ui/dialog";
import { toggleVariants } from "./ui/toggle";

export function money(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? `$${value}` : `$${value.toFixed(2)}`;
}

/** A modal that only closes when no save is in flight. */
export function PriceDialog({
  title,
  description,
  busy = false,
  onClose,
  footer,
  className,
  children,
}: {
  title: ReactNode;
  description: ReactNode;
  busy?: boolean;
  onClose: () => void;
  footer: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <DialogPopup className={cn("max-w-xl", className)} showCloseButton={!busy}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogPanel className="space-y-4 text-sm">{children}</DialogPanel>
        <DialogFooter>{footer}</DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}

export function PriceSection({ title, htmlFor, children }: {
  title: ReactNode;
  htmlFor?: string;
  children: ReactNode;
}) {
  const Title = htmlFor ? "label" : "div";
  return (
    <div className="flex flex-col gap-1.5">
      <Title htmlFor={htmlFor} className="font-medium text-muted-foreground text-xs">{title}</Title>
      {children}
    </div>
  );
}

/** A selectable price option, pressed when it is the chosen one. */
export function PriceChip({ active, wide, note, onClick, children }: {
  active: boolean;
  wide?: boolean;
  note?: ReactNode;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      data-pressed={active ? "" : undefined}
      className={cn(
        toggleVariants({ variant: "outline", size: "sm" }),
        "justify-start tabular-nums",
        wide && "h-auto w-full flex-col items-start gap-0.5 py-1.5 text-left whitespace-normal sm:h-auto",
        active && "border-ring ring-1 ring-ring",
      )}
      onClick={onClick}
    >
      <span>{children}</span>
      {note ? <span className="font-normal text-muted-foreground text-xs">{note}</span> : null}
    </button>
  );
}

/** A bordered list of listings, title left and price outcome right. */
export function PriceRows({ children }: { children: ReactNode }) {
  return <ul className="flex flex-col divide-y overflow-hidden rounded-xl border bg-card">{children}</ul>;
}

export function PriceRow({ title, error, children }: { title: ReactNode; error?: boolean; children: ReactNode }) {
  return (
    <li className="flex items-center justify-between gap-3 px-3 py-2">
      <span className="min-w-0 truncate">{title}</span>
      <span className={cn("shrink-0 text-xs tabular-nums", error ? "text-destructive-foreground" : "text-muted-foreground")}>
        {children}
      </span>
    </li>
  );
}

export function PriceStatus({ error, children }: { error?: boolean; children: ReactNode }) {
  return (
    <p className={cn("text-xs", error ? "text-destructive-foreground" : "text-muted-foreground")} role={error ? "alert" : "status"}>
      {children}
    </p>
  );
}
