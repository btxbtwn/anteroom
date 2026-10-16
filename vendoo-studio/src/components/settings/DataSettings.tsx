import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { backupSummary, formatBytes } from "../backupSummary";
import { SettingsRow, SettingsSection } from "./SettingsLayout";

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
          title="Cannot back up safely"
          description={warning}
          status={<span className="text-error">Action needed</span>}
        />
      ) : null}
      <SettingsRow
        id="backup-status"
        title="Snapshots"
        description="Studio copies the database when it starts, every six hours, and before any update or schema change. Each copy is checked before it is kept. Old copies are dropped once they fill the retention budget."
        status={
          isLoading ? (
            "Checking…"
          ) : error ? (
            <span className="text-error">{(error as Error).message || "Could not read backups"}</span>
          ) : data && !data.can_snapshot && warning ? (
            <span className="text-error">{warning}</span>
          ) : latest ? (
            backupSummary(data)
          ) : (
            <span className="text-error">No backups yet</span>
          )
        }
        control={
          <button
            type="button"
            className="btn btn-sm btn-outline"
            onClick={() => snapshot.mutate()}
            disabled={snapshot.isPending || data?.can_snapshot === false}
          >
            {snapshot.isPending ? "Backing up…" : "Back up now"}
          </button>
        }
      />
      <SettingsRow
        id="backup-folder"
        title="Backup folder"
        description="Somewhere that is not this Mac: an external drive, a synced folder, or a network share. Every snapshot and every photo is copied there. Without one, the only copies are on this disk."
        status={
          folderError ? (
            <span className="text-error">{folderError}</span>
          ) : currentFolder ? (
            <>Copying to {currentFolder}</>
          ) : (
            <span className="text-error">Not set — backups stay on this Mac</span>
          )
        }
      >
        <div className="extension-load-path-row">
          <input
            type="text"
            className="input input-sm"
            placeholder="/Volumes/Backup/List This Studio"
            value={shownFolder}
            onChange={(event) => {
              setEdited(true);
              setFolder(event.target.value);
            }}
          />
          <button
            type="button"
            className="btn btn-sm btn-outline"
            onClick={() => saveFolder.mutate(shownFolder.trim() ? shownFolder.trim() : null)}
            disabled={saveFolder.isPending}
          >
            {saveFolder.isPending ? "Checking…" : "Save"}
          </button>
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
    ? "Checking…"
    : error
      ? ((error as Error).message || "Could not read the database")
      : data
        ? `${formatBytes(data.database_bytes)}${top ? ` · largest table ${top.table}` : ""}`
        : "—";

  return (
    <SettingsRow
      id="database-maintenance"
      title="Database"
      description="Listings and job history live here. Prune drops leftover job events; free space returns to the disk the next time you quit Studio."
      status={error ? <span className="text-error">{status}</span> : status}
      control={
        <button
          type="button"
          className="btn btn-sm btn-outline"
          onClick={() => prune.mutate()}
          disabled={prune.isPending || isLoading}
        >
          {prune.isPending ? "Pruning…" : "Prune leftover events"}
        </button>
      }
    >
      {data?.tables?.length ? (
        <div className="settings-hidden-list">
          {data.tables.slice(0, 5).map((row) => (
            <div key={row.table}>
              {row.table}
              {row.bytes != null ? ` · ${formatBytes(row.bytes)}` : ""} · {row.rows} rows
            </div>
          ))}
        </div>
      ) : null}
      {prune.data?.pruned && prune.data.pruned.deleted > 0 ? (
        <p className="text-sm opacity-70">
          Removed {prune.data.pruned.deleted} leftover event
          {prune.data.pruned.deleted === 1 ? "" : "s"}. Quit Studio to reclaim the disk space.
        </p>
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
        <p className="settings-row-desc">Looking up the data folder…</p>
      ) : error ? (
        <p className="settings-row-desc text-error">{(error as Error).message || "Could not load data folder"}</p>
      ) : (
        <div className="extension-load-path-row">
          <code className="extension-load-path-code">{path}</code>
          <button type="button" className="btn btn-sm btn-outline" onClick={() => void copy()}>
            {copied ? "Copied" : "Copy path"}
          </button>
        </div>
      )}
    </SettingsRow>
  );
}

export function DataSettings() {
  return (
    <>
      <BackupsSection />
      <SettingsSection id="storage" title="Storage">
        <DataFolderRow />
        <DatabaseMaintenanceRow />
      </SettingsSection>
    </>
  );
}
