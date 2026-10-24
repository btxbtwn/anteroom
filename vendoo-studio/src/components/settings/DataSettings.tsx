import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { backupSummary, formatBytes } from "../backupSummary";
import { VendooImportButton } from "../VendooImportButton";
import { CheckIcon, CopyIcon, TriangleAlertIcon } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Spinner } from "../ui/spinner";
import { SettingsCode, SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

function BackupsSection() {
  const queryClient = useQueryClient();
  const { data, error, isLoading } = useQuery({
    queryKey: ["backups"],
    queryFn: api.backups.list,
  });
  const [folder, setFolder] = useState("");
  const [folderError, setFolderError] = useState<string | null>(null);
  const [edited, setEdited] = useState(false);

  const currentFolder = data?.folder || "";
  const shownFolder = edited ? folder : currentFolder;

  const snapshot = useMutation({
    mutationFn: api.backups.create,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backups"] }),
  });

  const saveFolder = useMutation({
    mutationFn: (next: string | null) => api.backups.setFolder(next),
    onSuccess: () => {
      setFolderError(null);
      setEdited(false);
      void queryClient.invalidateQueries({ queryKey: ["backups"] });
    },
    onError: (err: Error) => setFolderError(err.message || "Could not use that folder"),
  });

  const latest = data?.latest;
  const warning = data?.warning;

  return (
    <SettingsSection id="backups" title="Backups">
      {warning ? (
        <SettingsRow
          id="backup-warning"
          title={
            <>
              <TriangleAlertIcon className="size-3.5 text-warning" />
              Cannot back up safely
            </>
          }
          description={warning}
          status={<span className="text-warning-foreground">Action needed</span>}
        />
      ) : null}
      <SettingsRow
        id="backup-status"
        title="Snapshots"
        description="Anteroom copies the database when it starts, every six hours, and before any update or schema change. Each copy is checked before it is kept. Old copies are dropped once they fill the retention budget."
        status={
          isLoading ? (
            <span className="inline-flex items-center gap-1.5">
              <Spinner size="xs" tone="muted" />
              Checking
            </span>
          ) : error ? (
            <span className="text-destructive-foreground">{(error as Error).message || "Could not read backups"}</span>
          ) : data && !data.can_snapshot && warning ? (
            <span className="text-destructive-foreground">{warning}</span>
          ) : latest ? (
            backupSummary(data)
          ) : (
            <span className="text-warning-foreground">No backups yet</span>
          )
        }
        control={
          <Button
            size="xs"
            variant="outline"
            onClick={() => snapshot.mutate()}
            disabled={snapshot.isPending || data?.can_snapshot === false}
          >
            {snapshot.isPending ? <Spinner /> : null}
            {snapshot.isPending ? "Backing up" : "Back up now"}
          </Button>
        }
      />
      <SettingsRow
        id="backup-folder"
        title="Backup folder"
        description="Somewhere that is not this Mac: an external drive, a synced folder, or a network share. Every snapshot and every photo is copied there. Without one, the only copies are on this disk."
        status={
          folderError ? (
            <span className="text-destructive-foreground">{folderError}</span>
          ) : currentFolder ? (
            <>Copying to {currentFolder}</>
          ) : (
            <span className="text-warning-foreground">Not set — backups stay on this Mac</span>
          )
        }
      >
        <div className="flex min-w-0 items-center gap-2">
          <Input
            size="sm"
            font="mono"
            aria-label="Backup folder"
            placeholder="/Volumes/Backup/Anteroom"
            value={shownFolder}
            spellCheck={false}
            onChange={(event) => {
              setEdited(true);
              setFolder(event.target.value);
            }}
          />
          <Button
            size="xs"
            variant="outline"
            onClick={() => saveFolder.mutate(shownFolder.trim() ? shownFolder.trim() : null)}
            disabled={saveFolder.isPending}
          >
            {saveFolder.isPending ? <Spinner /> : null}
            {saveFolder.isPending ? "Checking" : "Save"}
          </Button>
        </div>
      </SettingsRow>
    </SettingsSection>
  );
}

function DatabaseMaintenanceRow() {
  const queryClient = useQueryClient();
  const { data, error, isLoading } = useQuery({
    queryKey: ["settings-database"],
    queryFn: api.settings.database,
  });
  const prune = useMutation({
    mutationFn: api.settings.pruneDatabase,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings-database"] });
      void queryClient.invalidateQueries({ queryKey: ["backups"] });
    },
  });

  const top = data?.tables?.[0];
  const status = isLoading
    ? "Checking"
    : error
      ? ((error as Error).message || "Could not read the database")
      : data
        ? `${formatBytes(data.database_bytes)}${top ? ` · largest table ${top.table}` : ""}`
        : "—";

  return (
    <SettingsRow
      id="database-maintenance"
      title="Database"
      description="Listings and job history live here. Prune drops leftover job events; free space returns to the disk the next time you quit Anteroom."
      status={error ? <span className="text-destructive-foreground">{status}</span> : status}
      control={
        <Button size="xs" variant="outline" onClick={() => prune.mutate()} disabled={prune.isPending || isLoading}>
          {prune.isPending ? <Spinner /> : null}
          {prune.isPending ? "Pruning" : "Prune leftover events"}
        </Button>
      }
    >
      {data?.tables?.length ? (
        <ul className="divide-y divide-border/50 overflow-hidden rounded-lg border border-border/60">
          {data.tables.slice(0, 5).map((row) => (
            <li key={row.table} className="flex min-w-0 items-center gap-3 px-3 py-2 text-xs">
              <span className="min-w-0 flex-1 truncate font-mono text-foreground">{row.table}</span>
              {row.bytes != null ? <span className="text-muted-foreground tabular-nums">{formatBytes(row.bytes)}</span> : null}
              <span className="w-24 text-right text-muted-foreground tabular-nums">{row.rows} rows</span>
            </li>
          ))}
        </ul>
      ) : null}
      {prune.data?.pruned && prune.data.pruned.deleted > 0 ? (
        <SettingsNote tone="success">
          Removed {prune.data.pruned.deleted} leftover event
          {prune.data.pruned.deleted === 1 ? "" : "s"}. Quit Anteroom to reclaim the disk space.
        </SettingsNote>
      ) : null}
    </SettingsRow>
  );
}

function DataFolderRow() {
  const { data, error, isLoading } = useQuery({
    queryKey: ["settings-data-folder"],
    queryFn: api.settings.dataFolder,
  });
  const [copied, setCopied] = useState(false);
  const path = data?.path || "";

  const copy = async () => {
    if (!path) return;
    await navigator.clipboard.writeText(path);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <SettingsRow
      id="data-folder"
      title="Data folder"
      description="Listings, photos, drafts, hidden fields, fill logs, settings, and logs stay here when you reinstall the app. API keys remain in macOS Keychain on this Mac."
    >
      {isLoading ? (
        <SettingsNote className="flex items-center gap-1.5">
          <Spinner size="xs" tone="muted" />
          Looking up the data folder
        </SettingsNote>
      ) : error ? (
        <SettingsNote tone="error">{(error as Error).message || "Could not load data folder"}</SettingsNote>
      ) : (
        <div className="flex min-w-0 items-center gap-1 rounded-md border border-border/70 bg-muted/40 py-0.5 pr-0.5 pl-2">
          <SettingsCode className="min-w-0 flex-1 truncate bg-transparent px-0">{path}</SettingsCode>
          <Button size="xs" variant="ghost-muted" onClick={() => void copy()}>
            {copied ? <CheckIcon /> : <CopyIcon />}
            {copied ? "Copied" : "Copy path"}
          </Button>
        </div>
      )}
    </SettingsRow>
  );
}

export function DataSettings() {
  return (
    <>
      <SettingsSection id="vendoo-sync" title="Vendoo">
        <SettingsRow
          title="Sync from Vendoo"
          description="Bring in every Vendoo item with its photos. Changed items are refreshed, and listings whose Vendoo item was deleted are removed."
          control={<VendooImportButton variant="settings" />}
        />
      </SettingsSection>
      <BackupsSection />
      <SettingsSection id="storage" title="Storage">
        <DataFolderRow />
        <DatabaseMaintenanceRow />
      </SettingsSection>
    </>
  );
}
