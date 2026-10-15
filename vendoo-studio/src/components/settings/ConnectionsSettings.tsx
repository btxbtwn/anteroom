import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ConnectChromeButton } from "../ConnectChromeButton";
import { ExtensionLoadPath } from "../ExtensionLoadPath";
import { SettingsRow, SettingsSection } from "./SettingsLayout";

function CategoryTreesRow() {
  const queryClient = useQueryClient();
  const {data, error} = useQuery({queryKey: ["category-trees"], queryFn: api.catalog.status,
    refetchInterval: 3000});
  const sync = useMutation({mutationFn: api.catalog.sync,
    onSuccess: () => queryClient.invalidateQueries({queryKey: ["category-trees"]})});
  return <SettingsRow id="category-trees" title="Marketplace category trees"
    description="Extract the full General, eBay, Poshmark, Mercari, Depop, and Etsy trees from Vendoo. Keep Chrome connected. Interrupted extraction resumes from its last saved branch."
    control={<button className="btn btn-sm btn-outline" disabled={data?.running || data?.complete || sync.isPending}
      onClick={() => sync.mutate()}>{data?.complete ? "Complete" : data?.running ? "Extracting…" : "Extract / resume"}</button>}>
    {Object.entries(data?.marketplaces || {}).map(([marketplace, tree]) =>
      <p className="settings-row-desc" key={marketplace}>
        {marketplace === "general" ? "General" : marketplace}: {tree.nodes.toLocaleString()} categories · {tree.status}
        {tree.pending_branches > 0 ? ` · ${tree.pending_branches} branches remaining` : ""}
        {tree.error ? ` — ${tree.error}` : ""}
      </p>)}
    {(error || sync.error) && <p role="alert">{(error || sync.error)?.message}</p>}
  </SettingsRow>;
}

export function ConnectionsSettings() {
  return (
    <>
      <SettingsSection id="connections" title="Vendoo">
        <SettingsRow
          title="Vendoo in Chrome"
          description="Connect Chrome opens Vendoo in your everyday Chrome and reloads Studio's listing extension so it matches this build. Send to Vendoo creates or updates a draft over Vendoo's API using that signed-in session."
          control={<ConnectChromeButton className="btn btn-sm btn-outline" />}
        />
        <SettingsRow
          id="extension-folder"
          title="Listing extension folder"
          description="Copy this path. In chrome://extensions turn on Developer mode, click Load unpacked, press Control-Shift-G (⌘⇧G) to search for the folder, paste the path, then Open. Studio overwrites this folder on launch so Chrome and Studio stay on the same files."
        >
          <ExtensionLoadPath compact hideHint />
        </SettingsRow>
        <CategoryTreesRow />
      </SettingsSection>
      <SettingsSection id="remote-access" title="Remote access">
        <TailscaleHttpsRow />
      </SettingsSection>
    </>
  );
}

type TailscaleStatus = Awaited<ReturnType<typeof api.settings.tailscale>>;

function TailscaleHttpsRow() {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["settings-tailscale"],
    queryFn: api.settings.tailscale,
    refetchInterval: 5000,
  });
  const enableMutation = useMutation({
    mutationFn: api.settings.enableTailscale,
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-tailscale"], payload);
    },
  });
  const disableMutation = useMutation({
    mutationFn: api.settings.disableTailscale,
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-tailscale"], payload);
    },
  });
  const busy = enableMutation.isPending || disableMutation.isPending;
  const actionError =
    (enableMutation.error as Error | null)?.message ||
    (disableMutation.error as Error | null)?.message ||
    null;

  async function copyUrl(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  const control = (() => {
    if (isPending || !data) {
      return <span className="settings-row-value">Checking…</span>;
    }
    if (data.enabled) {
      return (
        <button
          type="button"
          className="btn btn-sm btn-outline"
          disabled={busy || data.state === "conflict" || data.funnel}
          onClick={() => disableMutation.mutate()}
        >
          {disableMutation.isPending ? "Disabling…" : "Disable"}
        </button>
      );
    }
    const canEnable = data.installed && Boolean(data.dns_name) && data.state !== "conflict" && !data.funnel;
    return (
      <button
        type="button"
        className="btn btn-sm btn-outline"
        disabled={busy || !canEnable}
        onClick={() => enableMutation.mutate()}
      >
        {enableMutation.isPending ? "Enabling…" : "Enable"}
      </button>
    );
  })();

  return (
    <SettingsRow
      id="tailscale-https"
      title="Tailscale HTTPS"
      description="Share this Mac's Studio with your phone over your private Tailnet. Studio stays on 127.0.0.1; Tailscale proxies HTTPS. Funnel stays off."
      control={control}
    >
      {isError ? (
        <p className="settings-row-desc text-error" role="alert">
          {(error as Error).message || "Could not read Tailscale status"}
        </p>
      ) : null}
      {data ? <TailscaleStatusDetails data={data} copied={copied} onCopy={copyUrl} /> : null}
      {actionError ? (
        <p className="settings-row-desc text-error" role="alert">
          {actionError}
        </p>
      ) : null}
    </SettingsRow>
  );
}

function TailscaleStatusDetails({
  data,
  copied,
  onCopy,
}: {
  data: TailscaleStatus;
  copied: boolean;
  onCopy: (url: string) => void;
}) {
  if (!data.installed) {
    return (
      <p className="settings-row-desc">
        Install Tailscale on this Mac and your phone, sign in to the same account, then enable here.
      </p>
    );
  }
  if (!data.dns_name) {
    return (
      <p className="settings-row-desc text-error" role="alert">
        {data.error || "Sign in to Tailscale on this Mac first."}
      </p>
    );
  }
  if (data.error && !data.enabled) {
    return (
      <p className="settings-row-desc text-error" role="alert">
        {data.error}
      </p>
    );
  }
  if (!data.enabled || !data.url) {
    return (
      <p className="settings-row-desc">
        After Enable, open the HTTPS link on any device on your Tailnet — same Studio, same listings.
      </p>
    );
  }
  return (
    <div className="settings-tailscale-url">
      <code className="settings-row-code">{data.url}</code>
      <div className="settings-tailscale-actions">
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => onCopy(data.url!)}>
          {copied ? "Copied" : "Copy link"}
        </button>
        <a className="btn btn-sm btn-ghost" href={data.url} target="_blank" rel="noreferrer">
          Open
        </a>
      </div>
      <p className="settings-row-desc">
        On your phone: Tailscale app signed in → Safari/Chrome → paste this link. You are not running a second Studio.
      </p>
    </div>
  );
}
