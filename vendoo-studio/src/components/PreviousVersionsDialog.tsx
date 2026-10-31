import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type PublishedVersion, type VersionPlan } from "../api/client";
import { confirmDialog } from "../ui/confirmDialog";
import { waitForReload } from "./UpdateButton";
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
import { Spinner } from "./ui/spinner";

function formatWhen(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

/** The question asked before switching, which depends on what happens to the data. */
export function installVersionMessage(plan: Extract<VersionPlan, { action: "keep" | "restore" }>): string {
  const name = `Anteroom ${plan.version || plan.tag}`;
  if (plan.action === "keep") {
    return `Install ${name} and restart? Your listings stay as they are.`;
  }
  return (
    `${name} uses an older database layout, so it can't open your current data. ` +
    `Installing it restores your backup from ${formatWhen(plan.snapshot.taken_at)}. ` +
    "Anything you changed after that won't appear in this version. " +
    "Your current data is saved as a backup first (named “pre-rollback”), so it isn't lost."
  );
}

/** Settings → About → Previous versions: go back to an earlier published build. */
export function PreviousVersionsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["published-versions"],
    queryFn: api.updates.versions,
    enabled: open,
    staleTime: 60 * 1000,
  });
  const [checking, setChecking] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const install = useMutation({
    mutationFn: ({ tag, snapshot }: { tag: string; snapshot?: string | null }) =>
      api.updates.installVersion(tag, snapshot),
    onSuccess: async (result) => {
      if (result.reloading) await waitForReload();
    },
    onError: (err: Error) => setProblem(err.message),
  });

  async function choose(version: PublishedVersion) {
    setProblem(null);
    setChecking(version.tag);
    let plan: VersionPlan;
    try {
      plan = await api.updates.versionPlan(version.tag);
    } catch (err) {
      setProblem((err as Error).message);
      return;
    } finally {
      setChecking(null);
    }
    if (plan.action === "blocked") {
      setProblem(plan.reason);
      return;
    }
    const confirmed = await confirmDialog(installVersionMessage(plan), {
      variant: plan.action === "restore" ? "destructive" : "default",
      confirmLabel: plan.action === "restore" ? "Restore and install" : "Install and restart",
    });
    if (!confirmed) return;
    install.mutate({ tag: version.tag, snapshot: plan.action === "restore" ? plan.snapshot.name : null });
  }

  const versions = data?.versions || [];
  const busy = checking !== null || install.isPending || install.isSuccess;

  return (
    <Dialog open={open} onOpenChange={(next) => (next || busy ? undefined : onClose())}>
      <DialogPopup className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Previous versions</DialogTitle>
          <DialogDescription>
            Go back to an earlier build if the current one isn't working. Updating again returns you to the newest build.
          </DialogDescription>
        </DialogHeader>
        <DialogPanel className="space-y-3">
          {problem || data?.error ? (
            <p className="text-sm text-destructive-foreground" role="alert">{problem || data?.error}</p>
          ) : null}
          {data && !data.packaged ? (
            <p className="text-sm text-muted-foreground">Earlier versions can only be installed in the Mac app.</p>
          ) : null}
          {isLoading ? (
            <div className="space-y-3" role="status" aria-label="Loading versions">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-56" />
            </div>
          ) : error ? (
            <p className="text-sm text-destructive-foreground">{(error as Error).message}</p>
          ) : versions.length === 0 && !data?.error ? (
            <p className="text-sm text-muted-foreground">No earlier builds have been published yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {versions.map((version) => (
                <li key={version.tag} className="flex items-center gap-3 py-2 text-sm">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-semibold text-foreground">{version.version}</span>
                      {version.installed ? <Badge variant="info">Installed</Badge> : null}
                      <span className="text-xs text-muted-foreground">{formatWhen(version.published_at)}</span>
                    </div>
                    {version.title ? <p className="truncate text-muted-foreground">{version.title}</p> : null}
                  </div>
                  {version.installed ? null : (
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={busy || !data?.packaged}
                      aria-busy={checking === version.tag || undefined}
                      onClick={() => void choose(version)}
                    >
                      {checking === version.tag || (install.variables?.tag === version.tag && busy) ? <Spinner size="xs" /> : null}
                      Install
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </DialogPanel>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" disabled={busy} />}>Close</DialogClose>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}
