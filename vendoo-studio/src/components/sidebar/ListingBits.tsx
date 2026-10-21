import type { ReactNode } from "react";
import { CheckIcon, ChevronDownIcon, ImageIcon } from "lucide-react";
import { cn } from "../../lib/utils";

/** Square cover thumbnail shared by sidebar rows, suggestions and bulk dialogs. */
export function ListingThumb({ src, dimmed = false, className }: { src?: string | null; dimmed?: boolean; className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "flex size-9.5 flex-none items-center justify-center overflow-hidden rounded-md bg-sidebar-control-surface text-sidebar-muted-foreground ring-1 ring-sidebar-border ring-inset",
        className,
      )}
    >
      {src ? (
        <img
          className={cn("block size-full object-cover transition-opacity", dimmed && "opacity-55")}
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
        />
      ) : (
        <ImageIcon className="size-4 opacity-70" />
      )}
    </div>
  );
}

/** Row checkbox while selecting listings for a bulk action. */
export function SelectCheckbox({
  checked,
  label,
  title,
  disabled,
  onToggle,
}: {
  checked: boolean;
  label: string;
  title?: string;
  disabled?: boolean;
  onToggle: (shift: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      title={title}
      disabled={disabled}
      className={cn(
        "relative inline-flex size-4 max-[900px]:size-5.5 flex-none cursor-pointer items-center justify-center rounded-[4px] pointer-coarse:after:absolute pointer-coarse:after:-inset-3 border outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40",
        checked ? "border-primary bg-primary text-primary-foreground" : "border-input bg-transparent",
      )}
      onClick={(event) => {
        event.stopPropagation();
        if (!disabled) onToggle(event.shiftKey);
      }}
    >
      {checked ? <CheckIcon className="size-3" strokeWidth={3} /> : null}
    </button>
  );
}

const STATUS_TONES: Record<string, string> = {
  draft: "text-warning-foreground",
  "in-progress": "text-info-foreground",
  listing: "text-violet-500 dark:text-violet-400",
  active: "text-success-foreground",
  sold: "text-primary",
  failed: "text-destructive-foreground",
};

/** Lifecycle word under a listing title, coloured by status. */
export function ListingStatusLabel({ status, title }: { status: string; title?: string }) {
  const key = status.replace(/_/g, "-");
  return (
    <span
      className={cn(
        "max-w-full truncate text-2xs font-medium capitalize leading-tight",
        STATUS_TONES[key] ?? "text-sidebar-muted-foreground",
      )}
      title={title}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}

/** Collapsible group heading in the sidebar (Suggestions, Settled). */
export function ShelfHeader({ label, expanded, onToggle }: { label: string; expanded: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      onClick={onToggle}
      className="mt-1.5 mb-0.5 flex h-8 max-[900px]:h-11 max-[900px]:my-0 w-full shrink-0 cursor-pointer items-center gap-2 rounded-[var(--control-radius)] px-2 text-left text-xs font-medium text-sidebar-muted-foreground/70 outline-none hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
    >
      <span className="shrink-0">{label}</span>
      <span className="h-px min-w-2 flex-1 bg-sidebar-border" aria-hidden="true" />
      <ChevronDownIcon className={cn("size-3 shrink-0 transition-transform", expanded && "rotate-180")} />
    </button>
  );
}

/** A small rounded tag beside the status: unsent edits, needs relist. */
export function ListingTag({ tone, title, children }: { tone: "info" | "warning"; title?: string; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full px-1.5 py-px text-2xs font-medium leading-tight",
        tone === "info" ? "bg-primary/12 text-primary" : "bg-warning/14 text-warning-foreground",
      )}
      title={title}
    >
      {children}
    </span>
  );
}
