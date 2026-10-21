import { cn } from "../lib/utils";
import { Spinner } from "./ui/spinner";
import { WorkingDuration } from "./WorkingDuration";

export function sendProgressLabel(step: string, photos?: { completed: number; total: number } | null): string {
  if (step === "vendoo_api_photos" && photos && photos.total > 0) {
    return `${photos.completed} of ${photos.total} photos uploaded`;
  }
  const labels: Record<string, string> = {
    vendoo_api_queued: "Waiting in the send queue…",
    vendoo_api_read: "Reading the Vendoo draft…",
    vendoo_api_reviewed: "Preparing the reviewed draft…",
    vendoo_api_categories: "Resolving marketplace categories…",
    vendoo_api_specifics: "Reading category fields from Vendoo…",
    vendoo_api_fields: "Filling marketplace fields…",
    vendoo_api_photos: "Uploading photos to Vendoo…",
    vendoo_api_create: "Creating the Vendoo draft…",
    vendoo_api_patch: "Saving marketplace fields…",
    vendoo_api_verify: "Checking the saved Vendoo draft…",
  };
  return labels[step] || "Working with Vendoo…";
}

/** Measured photo progress only; never a synthetic percentage. */
export function SendPhotoProgress({ photos, className }: {
  photos: { completed: number; total: number };
  className?: string;
}) {
  return (
    <progress
      className={cn(
        "h-1 w-full appearance-none overflow-hidden rounded-full bg-muted-foreground/12 [&::-moz-progress-bar]:rounded-full [&::-moz-progress-bar]:bg-success [&::-webkit-progress-bar]:bg-transparent [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-success",
        className,
      )}
      aria-label="Photos uploaded"
      max={photos.total}
      value={photos.completed}
    />
  );
}

export function SendProgress({
  label,
  startedAt,
  photos,
  className,
}: {
  label: string;
  startedAt?: string | null;
  photos?: { completed: number; total: number } | null;
  className?: string;
}) {
  const startedAtMs = startedAt ? Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/.test(startedAt) ? startedAt : `${startedAt}Z`) : NaN;
  return (
    <div className={cn("mt-2 flex min-w-0 flex-col gap-1.5", className)}>
      <div className="flex min-w-0 items-center gap-2" role="status">
        <Spinner size="sm" className="text-success" aria-hidden role={undefined} />
        <span className="min-w-0 flex-1 text-xs text-foreground">{label}</span>
        {Number.isFinite(startedAtMs) && (
          <WorkingDuration startedAtMs={startedAtMs} className="shrink-0 text-xs text-muted-foreground tabular-nums" />
        )}
      </div>
      {photos && photos.total > 0 && <SendPhotoProgress photos={photos} />}
    </div>
  );
}
