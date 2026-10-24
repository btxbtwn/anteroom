import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { EXTENSION_STATUS_POLL_MS, pollMs } from "../../api/polling";
import { extensionConnection } from "../ExtensionStatus";
import { StatusDot } from "../StatusDot";
import { CheckIcon, CopyIcon, ExternalLinkIcon } from "lucide-react";
import { api } from "../../api/client";
import { ConnectChromeButton } from "../ConnectChromeButton";
import { ExtensionLoadPath } from "../ExtensionLoadPath";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { Button, buttonVariants } from "../ui/button";
import { Spinner } from "../ui/spinner";
import { SettingsCode, SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

function CategoryTreesRow() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ["category-trees"],
    queryFn: api.catalog.status,
    refetchInterval: 3000,
  });
  const sync = useMutation({
    mutationFn: api.catalog.sync,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["category-trees"] }),
  });
  const trees = Object.entries(data?.marketplaces || {});
  return (
    <SettingsRow
      id="category-trees"
      title="Marketplace category trees"
      description="Extract the full General, eBay, Poshmark, Mercari, Depop, and Etsy trees from Vendoo. Keep Chrome connected. Interrupted extraction resumes from its last saved branch."
      control={
        <Button
          size="xs"
          variant="outline"
          disabled={data?.running || data?.complete || sync.isPending}
          onClick={() => sync.mutate()}
        >
          {data?.running ? <Spinner /> : null}
          {data?.complete ? "Complete" : data?.running ? "Extracting" : "Extract / resume"}
        </Button>
      }
    >
      {trees.length ? (
        <ul className="divide-y divide-border/50 overflow-hidden rounded-lg border border-border/60">
          {trees.map(([marketplace, tree]) => (
            <li key={marketplace} className="flex min-w-0 items-center gap-2.5 px-3 py-2 text-xs">
              <MarketplaceLogo id={marketplace === "general" ? "vendoo" : marketplace} label={marketplace} size={16} />
              <span className="min-w-20 font-medium capitalize text-foreground">{marketplace}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">
                {tree.nodes.toLocaleString()} categories
                {tree.pending_branches > 0 ? ` · ${tree.pending_branches} branches remaining` : ""}
                {tree.error ? ` · ${tree.error}` : ""}
              </span>
              <span
                className={
                  tree.error
                    ? "text-destructive-foreground"
                    : tree.status === "complete"
                      ? "text-success-foreground"
                      : "text-muted-foreground"
                }
              >
                {tree.status}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {error || sync.error ? <SettingsNote tone="error">{(error || sync.error)?.message}</SettingsNote> : null}
    </SettingsRow>
  );
}

function ExtensionConnectionRow() {
  const { data } = useQuery({
    queryKey: ["extension-status"],
    queryFn: api.extension.status,
    refetchInterval: () => pollMs(EXTENSION_STATUS_POLL_MS),
  });
  const connection = extensionConnection(data);
  return (
    <SettingsRow
      title="Extension"
      description={connection.detail}
      control={
        <span className="flex items-center gap-1.5 text-xs text-foreground">
          <StatusDot tone={connection.tone} />
          {connection.label}
        </span>
      }
    />
  );
}

export function ConnectionsSettings() {
  return (
    <>
      <SettingsSection id="connections" title="Vendoo">
        <ExtensionConnectionRow />
        <SettingsRow
          title="Vendoo in Chrome"
          description="Connect Chrome opens Vendoo in your everyday Chrome and reloads Anteroom's listing extension so it matches this build. Send to Vendoo creates or updates a draft over Vendoo's API using that signed-in session."
          control={<ConnectChromeButton className={buttonVariants({ size: "xs", variant: "outline" })} />}
        />
        <SettingsRow
          id="extension-folder"
          title="Listing extension folder"
          description="Copy this path. In chrome://extensions turn on Developer mode, click Load unpacked, press Control-Shift-G (⌘⇧G) to search for the folder, paste the path, then Open. Anteroom overwrites this folder on launch so Chrome and Anteroom stay on the same files."
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
      return (
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner size="sm" tone="muted" />
          Checking
        </span>
      );
    }
    if (data.enabled) {
      return (
        <Button
          size="xs"
          variant="outline"
          disabled={busy || data.state === "conflict" || data.funnel}
          onClick={() => disableMutation.mutate()}
        >
          {disableMutation.isPending ? <Spinner /> : null}
          {disableMutation.isPending ? "Disabling" : "Disable"}
        </Button>
      );
    }
    const canEnable = data.installed && Boolean(data.dns_name) && data.state !== "conflict" && !data.funnel;
    return (
      <Button size="xs" variant="outline" disabled={busy || !canEnable} onClick={() => enableMutation.mutate()}>
        {enableMutation.isPending ? <Spinner /> : null}
        {enableMutation.isPending ? "Enabling" : "Enable"}
      </Button>
    );
  })();

  return (
    <SettingsRow
      id="tailscale-https"
      title="Tailscale HTTPS"
      description="Share this Mac's Anteroom with your phone over your private Tailnet. Anteroom stays on 127.0.0.1; Tailscale proxies HTTPS. Funnel stays off."
      control={control}
    >
      {isError ? (
        <SettingsNote tone="error">{(error as Error).message || "Could not read Tailscale status"}</SettingsNote>
      ) : null}
      {data ? <TailscaleStatusDetails data={data} copied={copied} onCopy={copyUrl} /> : null}
      {actionError ? <SettingsNote tone="error">{actionError}</SettingsNote> : null}
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
      <SettingsNote>
        Install Tailscale on this Mac and your phone, sign in to the same account, then enable here.
      </SettingsNote>
    );
  }
  if (!data.dns_name) {
    return <SettingsNote tone="error">{data.error || "Sign in to Tailscale on this Mac first."}</SettingsNote>;
  }
  if (data.error && !data.enabled) {
    return <SettingsNote tone="error">{data.error}</SettingsNote>;
  }
  if (!data.enabled || !data.url) {
    return (
      <SettingsNote>
        After Enable, open the HTTPS link on any device on your Tailnet — same Anteroom, same listings.
      </SettingsNote>
    );
  }
  return (
    <div className="space-y-2">
      <div className="flex min-w-0 items-center gap-1 rounded-md border border-border/70 bg-muted/40 py-0.5 pr-0.5 pl-2">
        <SettingsCode className="min-w-0 flex-1 truncate bg-transparent px-0">{data.url}</SettingsCode>
        <Button size="xs" variant="ghost-muted" onClick={() => onCopy(data.url!)}>
          {copied ? <CheckIcon /> : <CopyIcon />}
          {copied ? "Copied" : "Copy link"}
        </Button>
        <Button
          size="xs"
          variant="ghost-muted"
          render={<a href={data.url} target="_blank" rel="noreferrer" />}
        >
          <ExternalLinkIcon />
          Open
        </Button>
      </div>
      <SettingsNote>
        On your phone: Tailscale app signed in → Safari/Chrome → paste this link. You are not running a second copy of Anteroom.
      </SettingsNote>
    </div>
  );
}
