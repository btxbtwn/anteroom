import { useState, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { EXTENSION_STATUS_POLL_MS, pollMs } from "../../api/polling";
import { extensionConnection } from "../ExtensionStatus";
import { StatusDot } from "../StatusDot";
import { CheckIcon, CopyIcon, ExternalLinkIcon } from "lucide-react";
import { api } from "../../api/client";
import type { DirectAccountStatus, DirectMarketplace } from "../../api/directConnections";
import {
  EXTENSION_SESSION_NAMES,
  chromeSessionPresentation,
  type ExtensionSessionMarket,
} from "../../api/marketplaceSessions";
import { ConnectChromeButton } from "../ConnectChromeButton";
import { ExtensionLoadPath } from "../ExtensionLoadPath";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { Button, buttonVariants } from "../ui/button";
import { Input } from "../ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "../ui/select";
import { Spinner } from "../ui/spinner";
import { Switch } from "../ui/switch";
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

const DIRECT_MARKETPLACES: { id: DirectMarketplace; name: string; what: string }[] = [
  {
    id: "ebay",
    name: "eBay",
    what: "Connect eBay for sale alerts. Send offers to interested buyers from Marketplaces → eBay → Offers.",
  },
  {
    id: "etsy",
    name: "Etsy",
    what: "List through Etsy’s official API, and receive sale notifications so you can delist sold items elsewhere.",
  },
];

const CHROME_MARKET_ORDER: ExtensionSessionMarket[] = ["poshmark", "mercari", "depop", "sellwild", "vinted"];

function MarketplaceSalesSwitch({
  name,
  enabled,
  pending,
  onChange,
}: {
  name: string;
  enabled: boolean;
  pending: boolean;
  onChange: (enabled: boolean) => void;
}) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
      Detect sales
      <Switch
        size="sm"
        checked={enabled}
        disabled={pending}
        aria-label={`Sale detection for ${name}`}
        onCheckedChange={onChange}
      />
    </span>
  );
}

function directAccountState(account: DirectAccountStatus | undefined): {
  tone: "success" | "warning" | "error" | "muted";
  label: string;
} {
  if (account?.connected) return { tone: "success", label: account.label ? `Connected as ${account.label}` : "Connected" };
  if (account?.pending) return { tone: "warning", label: "Waiting for sign-in" };
  if (account?.error) return { tone: "error", label: "Not connected" };
  return { tone: "muted", label: "Not connected" };
}

export function DirectConnectionRow({
  marketplace,
  account,
  salesSwitch,
  checkError,
}: {
  marketplace: (typeof DIRECT_MARKETPLACES)[number];
  account: DirectAccountStatus | undefined;
  salesSwitch?: ReactNode;
  checkError?: string | null;
}) {
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["direct-connections"] });
  const connect = useMutation({ mutationFn: () => api.directConnections.connect(marketplace.id), onSettled: refresh });
  const cancel = useMutation({ mutationFn: () => api.directConnections.cancel(marketplace.id), onSettled: refresh });
  const disconnect = useMutation({ mutationFn: () => api.directConnections.disconnect(marketplace.id), onSettled: refresh });
  const state = directAccountState(account);
  const busy = connect.isPending || cancel.isPending || disconnect.isPending;
  const error = (connect.error || disconnect.error)?.message || account?.error || null;

  const control = account?.connected ? (
    <Button size="xs" variant="outline" disabled={busy} onClick={() => disconnect.mutate()}>
      {disconnect.isPending ? <Spinner /> : null}
      Disconnect
    </Button>
  ) : account?.pending ? (
    <Button size="xs" variant="ghost" disabled={busy} onClick={() => cancel.mutate()}>
      Cancel
    </Button>
  ) : (
    <Button size="xs" variant="outline" disabled={busy} onClick={() => connect.mutate()}>
      {connect.isPending ? <Spinner /> : null}
      Connect
    </Button>
  );

  return (
    <SettingsRow
      id={`direct-${marketplace.id}`}
      title={
        <span className="flex items-center gap-2">
          <MarketplaceLogo id={marketplace.id} label={marketplace.name} size={16} />
          {marketplace.name}
        </span>
      }
      description={marketplace.what}
      status={
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <StatusDot tone={state.tone} />
          {state.label}
        </span>
      }
      control={
        <span className="flex flex-wrap items-center justify-end gap-2">
          {salesSwitch}
          {control}
        </span>
      }
    >
      {checkError ? <SettingsNote tone="error">{checkError}</SettingsNote> : null}
      {account?.pending && account.authorizeUrl ? (
        <SettingsNote>
          Finish signing in on the {marketplace.name} page that opened in your browser.{" "}
          <a className="underline underline-offset-2" href={account.authorizeUrl} target="_blank" rel="noreferrer">
            Open it again
          </a>
        </SettingsNote>
      ) : null}
      {error && !account?.pending ? <SettingsNote tone="error">{error}</SettingsNote> : null}
      {marketplace.id === "etsy" && account?.connected ? <EtsyListingDefaults /> : null}
      {marketplace.id === "ebay" ? <EbayInventoryLocationRow /> : null}
      {marketplace.id === "ebay" && account?.connected ? (
        <EbayListingDefaults reconnecting={connect.isPending} onReconnect={() => connect.mutate()} />
      ) : null}
    </SettingsRow>
  );
}

/** The shop profiles every new Etsy listing uses, chosen once instead of per listing. */
function EtsyListingDefaults() {
  const queryClient = useQueryClient();
  const profiles = useQuery({ queryKey: ["etsy-listing-profiles"], queryFn: () => api.etsy.listingProfiles(), staleTime: 60_000, retry: false });
  const defaults = useQuery({ queryKey: ["etsy-listing-defaults"], queryFn: () => api.etsy.listingDefaults(), retry: false });
  const save = useMutation({
    mutationFn: (next: { shipping: string; processing: string; returns: string }) =>
      api.etsy.setListingDefaults(next.shipping, next.processing, next.returns),
    onSuccess: (data) => queryClient.setQueryData(["etsy-listing-defaults"], data),
  });
  const shipping = defaults.data?.shipping_profile_id ?? "";
  const processing = defaults.data?.readiness_state_id ?? "";
  const returns = defaults.data?.return_policy_id ?? "";
  const choose = (next: { shipping?: string; processing?: string; returns?: string }) => {
    const value = {
      shipping: next.shipping ?? shipping, processing: next.processing ?? processing, returns: next.returns ?? returns,
    };
    if (value.shipping && value.processing && value.returns) save.mutate(value);
    else queryClient.setQueryData(["etsy-listing-defaults"], {
      shipping_profile_id: value.shipping || undefined,
      readiness_state_id: value.processing || undefined,
      return_policy_id: value.returns || undefined,
    });
  };
  const disabled = !profiles.data || save.isPending;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={shipping || null} onValueChange={(value) => choose({ shipping: value || "" })} disabled={disabled}
          items={profiles.data?.shipping.map((profile) => ({ value: profile.id, label: profile.title }))}>
          <SelectTrigger size="compact" className="w-52" aria-label="Default Etsy shipping profile"><SelectValue placeholder="Shipping profile" /></SelectTrigger>
          <SelectPopup>{profiles.data?.shipping.map((profile) => <SelectItem key={profile.id} value={profile.id}>{profile.title}</SelectItem>)}</SelectPopup>
        </Select>
        <Select value={processing || null} onValueChange={(value) => choose({ processing: value || "" })} disabled={disabled}
          items={profiles.data?.processing.map((profile) => ({ value: profile.id, label: profile.title }))}>
          <SelectTrigger size="compact" className="w-52" aria-label="Default Etsy processing profile"><SelectValue placeholder="Processing profile" /></SelectTrigger>
          <SelectPopup>{profiles.data?.processing.map((profile) => <SelectItem key={profile.id} value={profile.id}>{profile.title}</SelectItem>)}</SelectPopup>
        </Select>
        <Select value={returns || null} onValueChange={(value) => choose({ returns: value || "" })} disabled={disabled}
          items={profiles.data?.returns.map((profile) => ({ value: profile.id, label: profile.title }))}>
          <SelectTrigger size="compact" className="w-52" aria-label="Default Etsy return policy"><SelectValue placeholder="Return policy" /></SelectTrigger>
          <SelectPopup>{profiles.data?.returns.map((profile) => <SelectItem key={profile.id} value={profile.id}>{profile.title}</SelectItem>)}</SelectPopup>
        </Select>
        {save.isPending ? <Spinner /> : null}
      </div>
      {profiles.error || save.error ? <SettingsNote tone="error">{(save.error || profiles.error)?.message}</SettingsNote>
        : <SettingsNote>New Etsy listings use these profiles, with the package weight and size from each listing.</SettingsNote>}
    </div>
  );
}

function EbayInventoryLocationRow() {
  const queryClient = useQueryClient();
  const { data, error, isPending } = useQuery({
    queryKey: ["settings-ebay-inventory-location"],
    queryFn: api.settings.ebayInventoryLocation,
  });
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? data?.location ?? "";
  const dirty = value !== (data?.location ?? "");
  const save = useMutation({
    mutationFn: (location: string) => api.settings.setEbayInventoryLocation(location),
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-ebay-inventory-location"], { location: payload.location });
      setDraft(null);
    },
  });

  return (
    <div id="ebay-inventory-location" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-2">
        <Input
          size="sm"
          className="w-52"
          aria-label="eBay inventory location"
          placeholder="70125"
          value={isPending ? "" : value}
          disabled={isPending}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button
          size="sm"
          disabled={isPending || !value.trim() || !dirty || save.isPending}
          onClick={() => save.mutate(value.trim())}
        >
          {save.isPending ? <Spinner /> : null}
          {save.isPending ? "Saving" : "Save"}
        </Button>
      </div>
      <SettingsNote>
        ZIP code eBay ships from. Anteroom writes it on every listing, and creates the eBay inventory location from a 5-digit ZIP when eBay does not have one yet.
      </SettingsNote>
      {error ? <SettingsNote tone="error">{(error as Error).message || "Could not load inventory location"}</SettingsNote> : null}
      {save.error ? <SettingsNote tone="error">{(save.error as Error).message}</SettingsNote> : null}
    </div>
  );
}

const EBAY_DEFAULTS = [
  ["fulfillment", "fulfillment_policy_id", "eBay shipping policy"],
  ["payment", "payment_policy_id", "eBay payment policy"],
  ["return", "return_policy_id", "eBay return policy"],
  ["locations", "merchant_location_key", "eBay ships-from location"],
] as const;

/** Shown only when the account has more than one policy or location. One is used automatically. */
function EbayListingDefaults({ reconnecting, onReconnect }: { reconnecting: boolean; onReconnect: () => void }) {
  const queryClient = useQueryClient();
  const profiles = useQuery({ queryKey: ["ebay-listing-profiles"], queryFn: () => api.ebay.listingProfiles(), staleTime: 60_000, retry: false });
  const defaults = useQuery({ queryKey: ["ebay-listing-defaults"], queryFn: () => api.ebay.listingDefaults(), retry: false });
  const save = useMutation({
    mutationFn: (next: Partial<Record<(typeof EBAY_DEFAULTS)[number][1], string>>) => api.ebay.setListingDefaults(next),
    onSuccess: (data) => queryClient.setQueryData(["ebay-listing-defaults"], data),
  });
  const several = EBAY_DEFAULTS.filter(([kind]) => (profiles.data?.[kind].length ?? 0) > 1);
  if (!profiles.error && several.length === 0) return null;
  const current = (key: (typeof EBAY_DEFAULTS)[number][1]) => defaults.data?.[key] ?? "";
  const choose = (key: (typeof EBAY_DEFAULTS)[number][1], value: string) => {
    if (!value) return;
    const next: Partial<Record<(typeof EBAY_DEFAULTS)[number][1], string>> = {};
    for (const [, name] of EBAY_DEFAULTS) {
      const chosen = name === key ? value : current(name);
      if (chosen) next[name] = chosen;
    }
    save.mutate(next);
  };
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {several.map(([kind, key, label]) => (
          <Select key={key} value={current(key) || null} onValueChange={(value) => choose(key, value || "")} disabled={save.isPending}
            items={profiles.data?.[kind].map((option) => ({ value: option.id, label: option.title }))}>
            <SelectTrigger size="compact" className="w-52" aria-label={label}><SelectValue placeholder={label} /></SelectTrigger>
            <SelectPopup>{profiles.data?.[kind].map((option) => <SelectItem key={option.id} value={option.id}>{option.title}</SelectItem>)}</SelectPopup>
          </Select>
        ))}
        {save.isPending ? <Spinner /> : null}
      </div>
      {profiles.error && !save.error ? (
        <div className="flex flex-wrap items-center gap-2">
          <SettingsNote tone="error">{profiles.error.message}</SettingsNote>
          <Button size="xs" variant="outline" disabled={reconnecting} onClick={onReconnect}>
            {reconnecting ? <Spinner /> : null}
            Reconnect eBay
          </Button>
        </div>
      ) : save.error ? <SettingsNote tone="error">{save.error.message}</SettingsNote>
        : several.length > 0 ? <SettingsNote>New eBay listings use the policy and location you choose here.</SettingsNote> : null}
    </div>
  );
}

const CHROME_SESSION_DETAILS: Record<ExtensionSessionMarket, string> = {
  depop: "Lists on Depop with the account signed in to Chrome.",
  poshmark: "Lists on Poshmark with the account signed in to Chrome.",
  mercari: "Saves the Mercari sign-in on this Mac so listing keeps working after Chrome restarts.",
  sellwild: "Lists on Sellwild with the account signed in to Chrome.",
  vinted: "Lists on Vinted with the account signed in to Chrome. Upload runs only after you confirm List on Vinted. Sales on Vinted are not detected.",
};

function ChromeSessionRow({
  marketplace,
  chromeReady,
  salesSwitch,
  checkError,
}: {
  marketplace: ExtensionSessionMarket;
  chromeReady: boolean;
  salesSwitch?: ReactNode;
  checkError?: string | null;
}) {
  const queryClient = useQueryClient();
  const [hint, setHint] = useState<string | null>(null);
  const sessions = useQuery({
    queryKey: ["marketplace-sessions"],
    queryFn: api.marketplaceSessions.status,
    enabled: chromeReady,
    retry: false,
    refetchInterval: chromeReady ? 60_000 : false,
  });
  const connect = useMutation({
    mutationFn: () => api.marketplaceSessions.connect(marketplace),
    onSuccess: (result) => {
      setHint(result.signed_in ? null : "Finish signing in in the Chrome tab, then choose Sign in again.");
      void queryClient.invalidateQueries({ queryKey: ["marketplace-sessions"] });
    },
    onError: () => setHint(null),
  });
  const session = sessions.data?.sessions[marketplace];
  const state = chromeSessionPresentation(session, chromeReady);
  const name = EXTENSION_SESSION_NAMES[marketplace];
  const error = connect.error?.message || null;

  return (
    <SettingsRow
      id={`chrome-${marketplace}`}
      title={
        <span className="flex items-center gap-2">
          <MarketplaceLogo id={marketplace} label={name} size={16} />
          {name}
        </span>
      }
      description={CHROME_SESSION_DETAILS[marketplace]}
      status={
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <StatusDot tone={state.tone} />
          {state.label}
        </span>
      }
      control={
        <span className="flex flex-wrap items-center justify-end gap-2">
          {salesSwitch}
          <Button size="xs" variant="outline" disabled={!chromeReady || connect.isPending} onClick={() => connect.mutate()}>
            {connect.isPending ? <Spinner /> : null}
            {session?.state === "signed_in" ? "Sign in again" : "Sign in"}
          </Button>
        </span>
      }
    >
      {hint ? <SettingsNote>{hint}</SettingsNote> : null}
      {error ? <SettingsNote tone="error">{error}</SettingsNote> : null}
      {session?.state === "signed_in" && checkError ? <SettingsNote tone="error">{checkError}</SettingsNote> : null}
    </SettingsRow>
  );
}

function MarketplaceSection() {
  const queryClient = useQueryClient();
  const extension = useQuery({
    queryKey: ["extension-status"],
    queryFn: api.extension.status,
    refetchInterval: () => pollMs(EXTENSION_STATUS_POLL_MS),
  });
  const direct = useQuery({
    queryKey: ["direct-connections"],
    queryFn: api.directConnections.status,
    refetchInterval: (query) => {
      const accounts = query.state.data?.accounts;
      return accounts && Object.values(accounts).some((account) => account.pending) ? 2000 : 15000;
    },
  });
  const detection = useQuery({
    queryKey: ["sale-detection"],
    queryFn: () => api.settings.saleDetection(),
    refetchInterval: 60_000,
  });
  const saveDetection = useMutation({
    mutationFn: ({ marketplace, enabled }: { marketplace: string; enabled: boolean }) =>
      api.settings.setSaleDetection(marketplace, enabled),
    onSuccess: (data) => queryClient.setQueryData(["sale-detection"], data),
  });
  const saveAutodelist = useMutation({
    mutationFn: (autodelist: boolean) => api.settings.setSaleAutodelist(autodelist),
    onSuccess: (data) => queryClient.setQueryData(["sale-detection"], data),
  });
  const chromeReady = Boolean(extension.data?.connected);
  const accounts = direct.data?.accounts;
  const relay = direct.data?.relay;
  const enabled = (marketplace: string) => detection.data?.enabled?.[marketplace] !== false;
  const salesSwitch = (marketplace: string, name: string) => (
    <MarketplaceSalesSwitch
      name={name}
      enabled={enabled(marketplace)}
      pending={saveDetection.isPending}
      onChange={(next) => saveDetection.mutate({ marketplace, enabled: next })}
    />
  );
  const listening = DIRECT_MARKETPLACES
    .filter((marketplace) => accounts?.[marketplace.id]?.connected && enabled(marketplace.id))
    .map((marketplace) => marketplace.name);
  return (
    <SettingsSection id="marketplaces" title="Marketplaces">
      {DIRECT_MARKETPLACES.map((marketplace) => (
        <DirectConnectionRow
          key={marketplace.id}
          marketplace={marketplace}
          account={accounts?.[marketplace.id]}
          salesSwitch={salesSwitch(marketplace.id, marketplace.name)}
          checkError={detection.data?.checks?.[marketplace.id]}
        />
      ))}
      {CHROME_MARKET_ORDER.map((marketplace) => (
        <ChromeSessionRow
          key={marketplace}
          marketplace={marketplace}
          chromeReady={chromeReady}
          salesSwitch={marketplace === "vinted" ? undefined : salesSwitch(marketplace, EXTENSION_SESSION_NAMES[marketplace])}
          checkError={marketplace === "vinted" ? null : detection.data?.checks?.[marketplace]}
        />
      ))}
      <SettingsRow
        title="Where the sign-in lives"
        description="Depop, Poshmark, Sellwild, and Vinted stay signed in through Chrome's cookies. Mercari's listing sign-in is saved in the extension on this Mac. Anteroom never stores those cookies or tokens. When one expires, Anteroom asks you to sign in again."
      />
      <SettingsRow
        id="sale-detection"
        title="Sale detection"
        description="Turn Detect sales on for a marketplace to hear when something sells there. Nothing is taken down unless Delist the other marketplaces is on. eBay and Etsy send the sale themselves. Chrome reads your Depop and Mercari sales, and compares your Poshmark and Sellwild closets with everything Anteroom still has listed there. The shops take turns, a few times an hour, with an uneven gap between checks. A sale counts only for an item Anteroom still shows as listed, so items you already sold, delisted, or marked not listed are skipped. Anteroom keeps only the listing and order IDs."
        status={
          listening.length || (relay && !relay.connected && Object.values(accounts ?? {}).some((account) => account.connected)) ? (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <StatusDot tone={relay?.connected ? "success" : "warning"} />
              {relay?.connected
                ? `Listening for ${listening.join(" and ")}`
                : relay?.lastError || "Connecting"}
            </span>
          ) : null
        }
      >
        {saveDetection.error ? <SettingsNote tone="error">{saveDetection.error.message}</SettingsNote> : null}
      </SettingsRow>
      <SettingsRow
        id="sale-autodelist"
        title="Delist the other marketplaces"
        description="When something sells, take it off the other marketplaces where Anteroom still has it listed. The place it sold stays as it is. Vinted is not taken down; delist it on Vinted. This stays off until you turn it on. If a delist can't finish, the sale notice says so and you can delist from the item."
        control={
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Switch
              size="sm"
              checked={detection.data?.autodelist === true}
              disabled={saveAutodelist.isPending}
              aria-label="Delist the other marketplaces when something sells"
              onCheckedChange={(checked) => saveAutodelist.mutate(checked)}
            />
          </span>
        }
      >
        {saveAutodelist.error ? <SettingsNote tone="error">{saveAutodelist.error.message}</SettingsNote> : null}
      </SettingsRow>
    </SettingsSection>
  );
}

export function ConnectionsSettings() {
  return (
    <>
      <MarketplaceSection />
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
