import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import MarkdownRenderer from "./MarkdownRenderer";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
} from "./ui/dialog";
import { Skeleton } from "./ui/skeleton";

/** "2026-09-22" → "Sep 22, 2026"; anything else is shown as it came. */
export function formatEntryDate(date: string | null | undefined): string {
  if (!date) return "";
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return date;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** What's new: every released version, newest first, with the running one marked. */
export function ChangelogDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["changelog"],
    queryFn: api.changelog,
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });
  const entries = data?.entries || [];

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogPopup className="max-w-xl">
        <DialogHeader>
          <DialogTitle>What's new</DialogTitle>
          <DialogDescription>Every change to List This Studio, newest first.</DialogDescription>
        </DialogHeader>
        <DialogPanel className="space-y-6">
          {isLoading ? (
            <div className="space-y-3" role="status" aria-label="Loading changelog">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-4/5" />
            </div>
          ) : error ? (
            <p className="text-sm text-destructive-foreground">
              {(error as Error).message || "Could not read the changelog."}
            </p>
          ) : entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No changelog entries yet.</p>
          ) : (
            entries.map((entry) => (
              <section key={entry.version} className="text-sm">
                <header className="mb-2 flex items-center gap-2">
                  <span className="font-mono text-sm font-semibold text-foreground">{entry.version}</span>
                  {entry.version === data?.version ? <Badge variant="info">Installed</Badge> : null}
                  <span className="ml-auto text-xs text-muted-foreground">{formatEntryDate(entry.date)}</span>
                </header>
                <MarkdownRenderer text={entry.body} />
              </section>
            ))
          )}
        </DialogPanel>
        <DialogFooter>
          <DialogClose render={<Button />}>Done</DialogClose>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}
