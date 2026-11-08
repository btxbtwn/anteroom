import { StudioImage } from "../StudioImage";
import { useState, type ReactNode } from "react";
import { CheckIcon, ImageIcon } from "lucide-react";
import { cn } from "../../lib/utils";

/** Square cover thumbnail shared by sidebar rows, suggestions and bulk dialogs. */
export function ListingThumb({ src, dimmed = false, className }: { src?: string | null; dimmed?: boolean; className?: string }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const showImage = Boolean(src) && src !== failedSrc;
  return (
    <div
      aria-hidden="true"
      className={cn(
        "flex size-9.5 flex-none items-center justify-center overflow-hidden rounded-md bg-sidebar-control-surface text-sidebar-muted-foreground ring-1 ring-sidebar-border ring-inset",
        className,
      )}
    >
      {showImage ? (
        <StudioImage
          className={cn("block size-full object-cover transition-opacity", dimmed && "opacity-55")}
          src={src ?? undefined}
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={() => setFailedSrc(src ?? null)}
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

// Status is a small dot beside a muted word, so a list of mostly-active items
// stays quiet. Only a failure colors the word itself.
const STATUS_DOTS: Record<string, string> = {
  draft: "bg-muted-foreground/50",
  "in-progress": "bg-info",
  listing: "bg-info",
  active: "bg-success",
  sold: "bg-foreground",
  failed: "bg-destructive",
};

/** Lifecycle word under a listing title, with a dot coloured by status. */
export function ListingStatusLabel({ status, title }: { status: string; title?: string }) {
  const key = status.replace(/_/g, "-");
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 truncate text-xs font-medium capitalize leading-tight",
        key === "failed" ? "text-destructive-foreground" : "text-sidebar-muted-foreground",
      )}
      title={title}
    >
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", STATUS_DOTS[key] ?? "bg-muted-foreground/50")} />
      {status.replace(/_/g, " ")}
    </span>
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
