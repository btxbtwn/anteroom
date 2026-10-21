import type { ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlertIcon, CircleCheckIcon, ClockIcon, InboxIcon, PlugIcon, XIcon } from "lucide-react";
import { api } from "../api/client";
import type { Job } from "../api/types";
import type { BulkRegenerate } from "./useBulkRegenerate";
import { toastManager } from "./ui/toast";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "./ui/empty";
import { Skeleton } from "./ui/skeleton";
import { Spinner } from "./ui/spinner";
import { SendPhotoProgress, sendProgressLabel } from "./SendProgress";
import { PageError, PageHeader, PageSection, Panel, WorkspacePage } from "./WorkspacePage";

export type QueueData = Awaited<ReturnType<typeof api.jobs.queue>>;
const ACTIVE = new Set(["queued", "awaiting_extension", "dispatched"]);

type Tone = "info" | "warning" | "success" | "error" | "secondary";

/** A row's status as a pill: what it is waiting on or how it ended. */
function StatusPill({ tone, icon, children }: { tone: Tone; icon: ReactNode; children: ReactNode }) {
  return (
    <Badge variant={tone} size="lg" className="max-w-48 gap-1.5 px-2 font-normal">
      {icon}
      <span className="truncate first-letter:uppercase">{children}</span>
    </Badge>
  );
}

const runningIcon = <Spinner size="xs" aria-hidden role={undefined} />;

function jobStatus(job: Job, index: number): { tone: Tone; icon: ReactNode; label: string } {
  switch (job.status) {
    case "queued":
      return { tone: "secondary", icon: <ClockIcon />, label: `Waiting · #${index + 1}` };
    case "awaiting_extension":
      return { tone: "warning", icon: <PlugIcon />, label: "Waiting for Chrome" };
    case "dispatched":
      return {
        tone: "info",
        icon: runningIcon,
        label: job.current_step ? sendProgressLabel(job.current_step, job.send_progress).replace(/…$/, "") : "Running",
      };
    case "completed":
      return { tone: "success", icon: <CircleCheckIcon />, label: "Saved draft" };
    case "failed":
      return { tone: "error", icon: <CircleAlertIcon />, label: "Failed" };
    case "cancelled":
      return { tone: "secondary", icon: <XIcon />, label: "Cancelled" };
    default:
      return { tone: "secondary", icon: <ClockIcon />, label: job.status.replace(/_/g, " ") };
  }
}

function QueueRow({ title, detail, error, status, progress, action, onOpen }: {
  title: string;
  detail: string;
  error?: string | null;
  status: ReactNode;
  progress?: ReactNode;
  action?: ReactNode;
  onOpen: () => void;
}) {
  return (
    <li className="flex min-w-0 flex-col gap-2 px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <button
            type="button"
            className="w-fit max-w-full cursor-pointer truncate text-left text-sm text-foreground outline-none hover:underline focus-visible:underline"
            onClick={onOpen}
          >
            {title}
          </button>
          <span className="truncate text-xs text-muted-foreground">{detail}</span>
        </div>
        {status}
        {action}
      </div>
      {progress}
      {error ? <p className="text-xs text-destructive-foreground">{error}</p> : null}
    </li>
  );
}

function QueueSkeleton() {
  return (
    <Panel role="status" aria-label="Loading queue…" aria-busy="true">
      {[0, 1, 2].map((row) => (
        <div key={row} className="flex items-center gap-3 px-4 py-3">
          <div className="flex flex-1 flex-col gap-1.5">
            <Skeleton className="h-3.5 w-56 max-w-full" />
            <Skeleton className="h-3 w-32" />
          </div>
          <Skeleton shape="pill" className="h-5 w-24" />
        </div>
      ))}
    </Panel>
  );
}

export function QueuePage({ data, loading, error, bulk, titles, onOpenListing }: {
  data?: QueueData;
  loading: boolean;
  error: Error | null;
  bulk: BulkRegenerate;
  titles: ReadonlyMap<string, string>;
  onOpenListing: (id: string) => void;
}) {
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: (job: Job) => api.jobs.cancel(job.id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["queue"] });
      client.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (err: Error) => toastManager.add({ type: "error", title: "Could not cancel send", description: err.message }),
  });
  const active = (data?.jobs ?? []).filter((j) => ACTIVE.has(j.status)).sort((a, b) => a.created_at.localeCompare(b.created_at));
  const recent = (data?.jobs ?? []).filter((j) => !ACTIVE.has(j.status));
  const bulkIds = new Set(bulk.pendingIds);
  const work = (data?.work ?? []).filter((w) => !bulkIds.has(w.conversation_id));
  const count = active.length + work.length + bulk.pendingIds.length;
  const renderJob = (job: Job, index: number) => {
    const status = jobStatus(job, index);
    const photos = job.status === "dispatched" && job.current_step === "vendoo_api_photos" ? job.send_progress : null;
    return (
      <QueueRow
        key={job.id}
        title={job.listing_title || titles.get(job.conversation_id) || "Untitled listing"}
        detail={job.mode === "schema_probe" ? "Discover fields" : job.vendoo_item_id && job.current_step !== "vendoo_api_created" ? "Update Vendoo draft" : "Send to Vendoo"}
        error={job.last_error}
        status={<StatusPill tone={status.tone} icon={status.icon}>{status.label}</StatusPill>}
        progress={photos && photos.total > 0 ? <SendPhotoProgress photos={photos} /> : null}
        action={ACTIVE.has(job.status) ? (
          <Button variant="outline" size="xs" disabled={cancel.isPending} onClick={() => cancel.mutate(job)}>Cancel</Button>
        ) : null}
        onOpen={() => onOpenListing(job.conversation_id)}
      />
    );
  };
  return (
    <WorkspacePage width="readable">
      <PageHeader
        title={<>Queue <span className="ms-1 text-base font-normal text-muted-foreground tabular-nums">{count}</span></>}
        description="Keep working while drafts are generated and approved sends are saved. Vendoo sends run one at a time. Nothing is published."
      />
      {error ? <PageError message={error.message} /> : null}
      <PageSection
        aria-label="In progress and waiting"
        title="In progress & waiting"
        actions={bulk.running ? (
          <Button variant="outline" size="xs" onClick={bulk.cancel} disabled={bulk.run?.cancelRequested}>Stop batch after active listings</Button>
        ) : null}
      >
        {loading ? <QueueSkeleton /> : null}
        {!loading && !error && count === 0 ? (
          <Panel divided={false}>
            <Empty size="compact" className="min-h-48">
              <EmptyHeader>
                <EmptyMedia variant="icon"><InboxIcon /></EmptyMedia>
                <EmptyTitle>No work waiting</EmptyTitle>
                <EmptyDescription>Generate a listing or click Send to Vendoo to add work here.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          </Panel>
        ) : null}
        {count > 0 ? (
          <Panel divided={false}>
            <ul className="divide-y divide-border/50">
              {bulk.pendingIds.map((id) => {
                const phase = bulk.run?.active.get(id)?.phase;
                return (
                  <QueueRow
                    key={`bulk-${id}`}
                    title={titles.get(id) || "Untitled listing"}
                    detail={bulk.run?.mode === "rewrite" ? "Regenerate listing" : "Generate draft"}
                    status={phase
                      ? <StatusPill tone="info" icon={runningIcon}>{phase}</StatusPill>
                      : <StatusPill tone="secondary" icon={<ClockIcon />}>Waiting</StatusPill>}
                    onOpen={() => onOpenListing(id)}
                  />
                );
              })}
              {work.map((w) => (
                <QueueRow
                  key={w.conversation_id}
                  title={w.title || "Untitled listing"}
                  detail={w.detail}
                  status={<StatusPill tone="info" icon={runningIcon}>Running</StatusPill>}
                  onOpen={() => onOpenListing(w.conversation_id)}
                />
              ))}
              {active.map(renderJob)}
            </ul>
          </Panel>
        ) : null}
      </PageSection>
      {recent.length > 0 ? (
        <PageSection aria-label="Recent sends" title="Recent sends">
          <Panel divided={false}>
            <ul className="divide-y divide-border/50">{recent.map(renderJob)}</ul>
          </Panel>
        </PageSection>
      ) : null}
    </WorkspacePage>
  );
}
