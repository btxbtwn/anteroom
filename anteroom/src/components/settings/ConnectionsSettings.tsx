import { useState, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { BUSY_POLL_MS, EXTENSION_STATUS_POLL_MS, IDLE_POLL_MS, pollMs } from "../../api/polling";
import { extensionConnection } from "../ExtensionStatus";
import { StatusDot } from "../StatusDot";
import { CheckIcon, CopyIcon, ExternalLinkIcon } from "lucide-react";
import { CHROME_WEB_STORE_URL } from "../../api/chromeBridge";
import { api } from "../../api/client";
import type { MarketplaceCloudResult, MarketplaceCloudSessions, MarketplaceCloudStatus } from "../../api/contract.gen";
import type { DirectAccountStatus, DirectMarketplace } from "../../api/directConnections";
import {
  EXTENSION_SESSION_NAMES,
  chromeSessionPresentation,
  type ExtensionSession,
  type ExtensionSessionMarket,
} from "../../api/marketplaceSessions";
import { ConnectChromeButton } from "../ConnectChromeButton";
import { ExtensionLoadPath } from "../ExtensionLoadPath";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { Button, buttonVariants } from "../ui/button";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "../ui/select";
import { Spinner } from "../ui/spinner";
import { Switch } from "../ui/switch";
import { SettingsCode, SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";
import { SaleDetectionTracker } from "./SaleDetectionTracker";
import { useWorkspaceRuntime } from "../../app/runtime";

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

const CHROME_MARKET_ORDER: ExtensionSessionMarket[] = ["poshmark", "mercari", "depop"];

/** Builds a row's Detect sales switch once the row knows whether its account can be checked. */
type SalesSwitch = (blockedReason: string | null) => ReactNode;

function MarketplaceSalesSwitch({
  name,
  enabled,
  pending,
  blockedReason,
  onChange,
}: {
  name: string;
  enabled: boolean;
  pending: boolean;
  /** Why sales can't be checked yet. The switch shows off until then; the saved choice is kept. */
  blockedReason: string | null;
  onChange: (enabled: boolean) => void;
}) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {blockedReason ?? "Detect sales"}
      <Switch
        size="sm"
        checked={blockedReason ? false : enabled}
        disabled={pending || Boolean(blockedReason)}
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
  salesSwitch?: SalesSwitch;
  checkError?: string | null;
}) {
  const queryClient = useQueryClient();
  const { hosted } = useWorkspaceRuntime();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["direct-connections"] });
  const connect = useMutation({
    mutationFn: () => api.directConnections.connect(marketplace.id),
    // The Mac opens the page itself. On the web, this browser does.
    onSuccess: (result) => { if (hosted) window.open(result.authorizeUrl, "_blank", "noopener,noreferrer"); },
    onSettled: refresh,
  });
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
          {salesSwitch?.(account?.connected ? null : "Connect to detect sales")}
          {control}
        </span>
      }
    >
      {checkError ? <SettingsNote tone="error">{checkError}</SettingsNote> : null}
      {account?.pending && account.authorizeUrl ? (
        <SettingsNote>
          {hosted ? `Finish signing in on the ${marketplace.name} page.` : `Finish signing in on the ${marketplace.name} page that opened in your browser.`}{" "}
          <a className="underline underline-offset-2" href={account.authorizeUrl} target="_blank" rel="noreferrer">
            Open it again
          </a>
        </SettingsNote>
      ) : null}
      {error && !account?.pending ? <SettingsNote tone="error">{error}</SettingsNote> : null}
      {marketplace.id === "etsy" && account?.connected ? <EtsyListingDefaults /> : null}
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
  mercari: "Keep desktop Chrome open for Mercari listing actions and sales checks. Anteroom Bridge remembers your sign-in after Chrome restarts.",
};

function SignInStatus({ state }: { state: ReturnType<typeof chromeSessionPresentation> }) {
  return (
    <span role="status" className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <StatusDot tone={state.tone} />
      {state.label}
    </span>
  );
}

function ChromeSessionRow({
  marketplace,
  chromeReady,
  session,
  checking,
  unavailable,
  salesSwitch,
  checkError,
  cloud,
  cloudConnected = false,
  hosted = false,
}: {
  marketplace: ExtensionSessionMarket;
  chromeReady: boolean;
  session?: ExtensionSession;
  checking?: boolean;
  unavailable?: string | null;
  salesSwitch?: SalesSwitch;
  checkError?: string | null;
  cloud?: ReactNode;
  cloudConnected?: boolean;
  hosted?: boolean;
}) {
  const queryClient = useQueryClient();
  const name = EXTENSION_SESSION_NAMES[marketplace];
  const helpId = `${marketplace}-chrome-sign-in-help`;
  const help = cloud
    ? "Keep your sign-in in your browser. Leave Chrome open for listing actions and sales checks."
    : CHROME_SESSION_DETAILS[marketplace];
  const label = hosted || (session?.state !== "signed_in" && session?.state !== "expired") ? "Sign in" : "Sign in again";
  const connect = useMutation({
    mutationFn: async () => {
      const result = await api.marketplaceSessions.connect(marketplace);
      if (!result.ok) throw new Error(result.error || "Chrome could not confirm the sign-in. Try again.");
      return result;
    },
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["marketplace-sessions"] }); },
  });
  const waiting = connect.data && !connect.data.signed_in;
  const state = connect.isPending || connect.error || waiting
    ? { tone: "warning" as const, label: "Sign-in not confirmed" }
    : checking
      ? { tone: "muted" as const, label: "Checking Chrome…" }
      : chromeSessionPresentation(session, chromeReady, undefined, unavailable);
  const effectiveSignedIn = cloudConnected || (chromeReady && session?.state === "signed_in");
  const salesBlocked = effectiveSignedIn ? null : !chromeReady && !cloud ? "Connect Chrome to detect sales" : "Sign in to detect sales";
  const signIn = (
    <Button size="xs" variant="outline" className={hosted ? "pointer-coarse:h-11" : undefined}
      disabled={!chromeReady || connect.isPending}
      aria-label={hosted ? `Sign in to ${name} in Chrome` : undefined}
      aria-describedby={hosted ? helpId : undefined} onClick={() => connect.mutate()}>
      {connect.isPending ? <Spinner /> : null}
      {label}
    </Button>
  );
  const chromeFeedback = connect.error
    ? <SettingsNote tone="error">{connect.error.message}</SettingsNote>
    : waiting
      ? <SettingsNote role="status">Finish signing in in Chrome, then choose {label}.</SettingsNote>
      : null;

  return (
    <SettingsRow
      id={`chrome-${marketplace}`}
      title={<span className="flex items-center gap-2"><MarketplaceLogo id={marketplace} label={name} size={16} />{name}</span>}
      description={hosted ? undefined : help}
      status={hosted ? undefined : <SignInStatus state={state} />}
      control={
        <span className="flex flex-wrap items-center gap-2">
          {salesSwitch?.(salesBlocked)}
          {hosted ? null : signIn}
        </span>
      }
    >
      {hosted ? (
        <div className="grid gap-3 border-t border-border/50 pt-3 sm:grid-flow-col sm:auto-cols-fr">
          <div role="group" aria-label="Chrome" className="min-w-0 space-y-2">
            <p className="text-xs font-medium text-foreground">Chrome</p>
            <SettingsNote id={helpId}>{help}</SettingsNote>
            <SignInStatus state={state} />
            {signIn}
            {chromeFeedback}
          </div>
          {cloud}
        </div>
      ) : chromeFeedback}
      {effectiveSignedIn && checkError ? <SettingsNote tone="error">Sale check failed: {checkError}</SettingsNote> : null}
    </SettingsRow>
  );
}

const CLOUD_SESSIONS_KEY = ["cloud-sessions"] as const;
type CloudSignInPending = { until: number; previousConnection?: string | null };

function awaitingCloudSignIn(pending: CloudSignInPending | undefined, session: MarketplaceCloudStatus | undefined) {
  return Boolean(pending && pending.until > Date.now()
    && (!session?.cloud_connected || session.connected_at === pending.previousConnection));
}

function CloudSignIn({
  marketplace, session, statusError, checkedAt, pending, setPending,
}: {
  marketplace: ExtensionSessionMarket;
  session?: MarketplaceCloudStatus;
  statusError: boolean;
  checkedAt: number;
  pending?: CloudSignInPending;
  setPending: (pending?: CloudSignInPending) => void;
}) {
  const queryClient = useQueryClient();
  const { account } = useWorkspaceRuntime();
  const name = EXTENSION_SESSION_NAMES[marketplace];
  const helpId = `${marketplace}-cloud-sign-in-help`;
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: CLOUD_SESSIONS_KEY });
    void queryClient.invalidateQueries({ queryKey: ["marketplace-sessions"] });
  };
  const acceptStatus = async (result: MarketplaceCloudResult) => {
    const confirmed = result.session;
    if (confirmed) {
      // A GET started before Test or Disconnect may still carry the previous status.
      await queryClient.cancelQueries({ queryKey: CLOUD_SESSIONS_KEY });
      queryClient.setQueryData<MarketplaceCloudSessions>(CLOUD_SESSIONS_KEY, (old) => old && ({
        ...old, sessions: { ...old.sessions, [marketplace]: confirmed },
      }), { updatedAt: queryClient.getQueryState(CLOUD_SESSIONS_KEY)?.dataUpdatedAt });
    }
    refresh();
  };
  const signIn = useMutation({
    mutationFn: async () => {
      if (!account) throw new Error("Sign in to Anteroom first.");
      const { token, seller_region } = await api.cloudSessions.connectToken(marketplace);
      const { cloudExtension } = await import("../../api/cloudExtension");
      const result = await cloudExtension(account).connectCloudSession(marketplace, token, seller_region);
      if (!result.ok) throw new Error(result.error || `Open Anteroom in desktop Chrome with Anteroom Bridge to sign in to ${name} in Cloud.`);
    },
    onSuccess: () => {
      setPending({ until: Date.now() + 180_000, previousConnection: session?.connected_at });
      refresh();
    },
    onError: () => setPending(),
  });
  const verify = useMutation({ mutationFn: () => api.cloudSessions.verify(marketplace), onSuccess: acceptStatus, onError: refresh });
  const disconnect = useMutation({
    mutationFn: () => api.cloudSessions.disconnect(marketplace),
    onSuccess: (result) => { setPending(); return acceptStatus(result); },
    onError: refresh,
  });
  const waiting = awaitingCloudSignIn(pending, session);
  const testUnconfirmed = Boolean(verify.error || (verify.data && !verify.data.ok));
  let state: ReturnType<typeof chromeSessionPresentation>;
  if (statusError) state = { tone: "muted", label: "Could not check Cloud" };
  else if (!session) state = { tone: "muted", label: "Checking Cloud…" };
  else if (session.cloud_connected && testUnconfirmed) state = { tone: "warning", label: "Sign-in not verified" };
  else state = chromeSessionPresentation({
    state: session.cloud_connected ? "signed_in" : session.state === "expired" ? "expired" : "signed_out",
    expires_at: session.expires_at ?? null,
    label: session.label ?? null,
    expiring: session.expiring,
  }, true);
  const busy = signIn.isPending || verify.isPending || disconnect.isPending;
  const error = signIn.error?.message || verify.error?.message || verify.data?.error
    || disconnect.error?.message || disconnect.data?.error || session?.last_error;
  const verified = verify.data?.ok && session?.cloud_connected && !statusError
    && verify.data.session?.connected_at === session.connected_at;
  return (
    <div role="group" aria-label="Cloud" className="min-w-0 space-y-2 border-t border-border/50 pt-3 sm:border-t-0 sm:border-l sm:pt-0 sm:pl-3">
      <p className="text-xs font-medium text-foreground">Cloud</p>
      <SettingsNote id={helpId}>
        {marketplace === "depop"
          ? "Anteroom saves an encrypted sign-in for sales checks, imports, and standalone delists. Keep Chrome connected for drafts, publishing, edits, relists, and offers."
          : "Anteroom saves an encrypted sign-in so listing actions, sales checks, and enabled automations can run with Chrome closed."}
      </SettingsNote>
      <SignInStatus state={state} />
      {checkedAt && !statusError ? (
        <SettingsNote>Checked {new Date(checkedAt).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</SettingsNote>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button size="xs" variant="outline" className="pointer-coarse:h-11"
          disabled={busy} aria-label={`Sign in to ${name} in Cloud`} aria-describedby={helpId}
          onClick={() => { verify.reset(); disconnect.reset(); signIn.mutate(); }}>
          {signIn.isPending || waiting ? <Spinner /> : null}
          Sign in
        </Button>
        {session?.cloud_connected ? (
          <Button size="xs" variant="ghost" className="pointer-coarse:h-11"
            disabled={busy} aria-label={`Test ${name} cloud connection`}
            onClick={() => { signIn.reset(); disconnect.reset(); verify.mutate(); }}>
            {verify.isPending ? <Spinner /> : null}
            Test connection
          </Button>
        ) : null}
        {session?.cloud_connected || session?.state === "expired" ? (
          <Button size="xs" variant="ghost" className="pointer-coarse:h-11"
            disabled={busy} aria-label={`Disconnect ${name} from Cloud`}
            onClick={() => { signIn.reset(); verify.reset(); disconnect.mutate(); }}>
            {disconnect.isPending ? <Spinner /> : null}
            Disconnect
          </Button>
        ) : null}
      </div>
      {waiting ? <SettingsNote role="status">Confirm the cloud sign-in in the Anteroom Bridge popup.</SettingsNote> : null}
      {verify.isPending ? <SettingsNote role="status">Testing connection…</SettingsNote> : null}
      {verified ? <SettingsNote role="status">Cloud sign-in verified.</SettingsNote> : null}
      {error ? <SettingsNote tone="error">{error}</SettingsNote> : null}
    </div>
  );
}

function HostedChromeSessions({ salesSwitch, checks }: {
  salesSwitch?: (marketplace: string, name: string) => SalesSwitch;
  checks?: Record<string, string>;
}) {
  const [pending, setPending] = useState<Partial<Record<ExtensionSessionMarket, CloudSignInPending>>>({});
  const chrome = useQuery({
    queryKey: ["marketplace-sessions"],
    queryFn: api.marketplaceSessions.status,
    retry: false,
    refetchInterval: (query) => pollMs(query.state.status === "error" ? 15_000 : 60_000),
  });
  const chromeReady = chrome.isSuccess && chrome.data?.chrome_connected !== false;
  const chromeSessions = chrome.data?.chrome_sessions ?? chrome.data?.sessions;
  const cloud = useQuery({
    queryKey: CLOUD_SESSIONS_KEY,
    queryFn: ({ signal }) => api.cloudSessions.status({ signal }),
    retry: false,
    refetchOnWindowFocus: "always",
    refetchInterval: (query) => pollMs(CHROME_MARKET_ORDER.some(
      (market) => awaitingCloudSignIn(pending[market], query.state.data?.sessions[market]),
    ) ? BUSY_POLL_MS : IDLE_POLL_MS),
  });

  return (
    <>
      {CHROME_MARKET_ORDER.map((marketplace) => {
        const chromeSession = chromeSessions?.[marketplace];
        const cloudSession = cloud.data?.sessions[marketplace];
        return (
          <ChromeSessionRow key={marketplace} marketplace={marketplace} chromeReady={chromeReady}
            session={chromeSession?.mode === "cloud" ? undefined : chromeSession}
            checking={chrome.isPending} unavailable={chrome.error?.message}
            salesSwitch={salesSwitch?.(marketplace, EXTENSION_SESSION_NAMES[marketplace])} checkError={checks?.[marketplace]} hosted
            cloudConnected={cloudSession?.cloud_connected}
            cloud={cloud.data?.enabled.includes(marketplace) ? (
              <CloudSignIn marketplace={marketplace} session={cloudSession} statusError={cloud.isError}
                checkedAt={cloud.dataUpdatedAt} pending={pending[marketplace]}
                setPending={(attempt) => setPending((current) => ({ ...current, [marketplace]: attempt }))} />
            ) : null} />
        );
      })}
      <SettingsRow
        title="Sign-in setup"
        description="Start marketplace sign-in in desktop Chrome with Anteroom Bridge."
        control={
          <Button size="xs" variant="outline" render={<a href={CHROME_WEB_STORE_URL} target="_blank" rel="noopener noreferrer" />}>
            <ExternalLinkIcon />
            Chrome Web Store
          </Button>
        }
      >
        {cloud.isError ? <SettingsNote tone="error">Cloud status is unavailable. {cloud.error.message}</SettingsNote> : null}
        {chrome.isError ? <SettingsNote>{chrome.error.message}</SettingsNote> : null}
      </SettingsRow>
    </>
  );
}

function MarketplaceSection() {
  const queryClient = useQueryClient();
  // Chrome sign-ins and sale checks use the seller's connected browser on the web.
  const { hosted, features } = useWorkspaceRuntime();
  const sessionWorkflows = !hosted || features.has("marketplace_sessions");
  const extension = useQuery({
    queryKey: ["extension-status"],
    queryFn: api.extension.status,
    refetchInterval: () => pollMs(EXTENSION_STATUS_POLL_MS),
    enabled: !hosted,
  });
  const direct = useQuery({
    queryKey: ["direct-connections"],
    queryFn: api.directConnections.status,
    refetchInterval: (query) => {
      const accounts = query.state.data?.accounts;
      return accounts && Object.values(accounts).some((account) => account.pending) ? pollMs(BUSY_POLL_MS) : pollMs(15000);
    },
  });
  const detection = useQuery({
    queryKey: ["sale-detection"],
    queryFn: () => api.settings.saleDetection(),
    refetchInterval: () => pollMs(60_000),
    enabled: sessionWorkflows,
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
  const saveAutoAdjust = useMutation({
    mutationFn: (autoAdjust: boolean) => api.settings.setSaleAutoAdjustQuantity(autoAdjust),
    onSuccess: (data) => queryClient.setQueryData(["sale-detection"], data),
  });
  const chromeReady = Boolean(extension.data?.connected);
  const chromeSessions = useQuery({
    queryKey: ["marketplace-sessions"],
    queryFn: api.marketplaceSessions.status,
    enabled: !hosted && chromeReady,
    retry: false,
    refetchInterval: !hosted && chromeReady ? () => pollMs(60_000) : false,
  });
  const accounts = direct.data?.accounts;
  const relay = direct.data?.relay;
  const enabled = (marketplace: string) => detection.data?.enabled?.[marketplace] !== false;
  const salesSwitch = (marketplace: string, name: string): SalesSwitch => (blockedReason) => (
    <MarketplaceSalesSwitch
      name={name}
      enabled={enabled(marketplace)}
      pending={saveDetection.isPending}
      blockedReason={blockedReason}
      onChange={(next) => saveDetection.mutate({ marketplace, enabled: next })}
    />
  );
  const listening = DIRECT_MARKETPLACES
    .filter((marketplace) => accounts?.[marketplace.id]?.connected && enabled(marketplace.id))
    .map((marketplace) => marketplace.name);
  const autodelistRow = (
    <SettingsRow
      id="sale-autodelist"
      inlineControl
      title="Delist the other marketplaces"
      description="When something sells, take it off the other marketplaces it’s still listed on; a delist that doesn’t go through is tried again for two weeks after the sale, and the sidebar alerts you while a sold item is still listed."
      control={
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {saveAutodelist.isPending ? (
            <span role="status" className="inline-flex items-center gap-1">
              <Spinner size="sm" tone="muted" aria-hidden role={undefined} />
              Saving…
            </span>
          ) : null}
          {detection.data === undefined ? (
            detection.error ? <span>Unavailable</span> : <span role="status">Loading…</span>
          ) : (
            <Switch
              size="sm"
              checked={detection.data.autodelist === true}
              disabled={saveAutodelist.isPending}
              aria-label="Delist the other marketplaces when something sells"
              onCheckedChange={(checked) => saveAutodelist.mutate(checked)}
            />
          )}
        </span>
      }
    >
      {detection.error ? <SettingsNote tone="error">{detection.error.message}</SettingsNote> : null}
      {saveAutodelist.error ? <SettingsNote tone="error">{saveAutodelist.error.message}</SettingsNote> : null}
    </SettingsRow>
  );
  const autoAdjustRow = (
    <SettingsRow
      id="sale-auto-adjust-quantity"
      inlineControl
      title="Auto-adjust quantity"
      description="When a multi-quantity item sells with stock remaining, decrement quantities on other live marketplaces without delisting them. On Mercari (quantity capped at 1), automatically relist a fresh single-quantity listing and update tracking."
      control={
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {saveAutoAdjust.isPending ? (
            <span role="status" className="inline-flex items-center gap-1">
              <Spinner size="sm" tone="muted" aria-hidden role={undefined} />
              Saving…
            </span>
          ) : null}
          {detection.data === undefined ? (
            detection.error ? <span>Unavailable</span> : <span role="status">Loading…</span>
          ) : (
            <Switch
              size="sm"
              checked={detection.data.auto_adjust_quantity === true}
              disabled={saveAutoAdjust.isPending}
              aria-label="Auto-adjust quantity across marketplaces when stock remains"
              onCheckedChange={(checked) => saveAutoAdjust.mutate(checked)}
            />
          )}
        </span>
      }
    >
      {saveAutoAdjust.error ? <SettingsNote tone="error">{saveAutoAdjust.error.message}</SettingsNote> : null}
    </SettingsRow>
  );
  if (hosted) {
    return (
      <>
        <SettingsSection id="accounts" title="Accounts">
          {DIRECT_MARKETPLACES.map((marketplace) => (
            <DirectConnectionRow key={marketplace.id} marketplace={marketplace} account={accounts?.[marketplace.id]}
              salesSwitch={sessionWorkflows ? salesSwitch(marketplace.id, marketplace.name) : undefined} checkError={detection.data?.checks?.[marketplace.id]} />
          ))}
          <HostedChromeSessions salesSwitch={sessionWorkflows ? salesSwitch : undefined} checks={detection.data?.checks} />
          {sessionWorkflows ? (
            <>
              <SettingsRow id="sale-detection" title="Sale detection"
                description="Anteroom checks for sales through each marketplace’s active connection. Saved sales stay if orders can’t be read. With Delist the other marketplaces enabled, Anteroom can remove sold items from other marketplaces.">
                {saveDetection.error ? <SettingsNote tone="error">{saveDetection.error.message}</SettingsNote> : null}
              </SettingsRow>
              {autodelistRow}
              {autoAdjustRow}
            </>
          ) : null}
        </SettingsSection>
        {sessionWorkflows ? <SaleDetectionTracker /> : null}
      </>
    );
  }
  return (
    <>
      <SettingsSection id="accounts" title="Accounts">
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
            session={chromeSessions.data?.sessions[marketplace]}
            checking={chromeReady && chromeSessions.isPending}
            unavailable={chromeSessions.error?.message}
            salesSwitch={salesSwitch(marketplace, EXTENSION_SESSION_NAMES[marketplace])}
            checkError={detection.data?.checks?.[marketplace]}
          />
        ))}
        <SettingsRow
          title="Where the sign-in lives"
          description="These sign-ins live in Chrome and the extension, never in Anteroom, which asks you to sign in again when one expires."
        />
        <SettingsRow
          id="sale-detection"
          title="Sale detection"
          description="Detect sales checks a few times an hour (Poshmark by comparing your Poshmark closet) and tells you when a listed item sells; nothing comes down unless Delist the other marketplaces is on."
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
          {relay?.skippedEvent ? <SettingsNote tone="error">{relay.skippedEvent}</SettingsNote> : null}
        </SettingsRow>
        {autodelistRow}
        {autoAdjustRow}
      </SettingsSection>
      <SaleDetectionTracker />
    </>
  );
}

export function ConnectionsSettings() {
  const { hosted } = useWorkspaceRuntime();
  if (hosted) return <MarketplaceSection />;
  return (
    <>
      <MarketplaceSection />
      <SettingsSection id="connections" title="Chrome">
        <ExtensionConnectionRow />
        <SettingsRow
          title="Chrome"
          description="Optional for creating drafts and eBay or Etsy. Install Anteroom Bridge from the Chrome Web Store and connect desktop Chrome for Poshmark, Mercari and Depop actions."
          control={
            <span className="flex flex-wrap items-center justify-end gap-2">
              <Button
                size="xs"
                variant="outline"
                render={<a href={CHROME_WEB_STORE_URL} target="_blank" rel="noopener noreferrer" />}
              >
                <ExternalLinkIcon />
                Chrome Web Store
              </Button>
              <ConnectChromeButton className={buttonVariants({ size: "xs", variant: "outline" })} />
            </span>
          }
        />
        <SettingsRow
          id="extension-folder"
          title="Listing extension folder (Developer mode)"
          description="Only needed if loading unpacked instead of the Chrome Web Store: in chrome://extensions, turn on Developer mode, click Load unpacked, press ⌘⇧G, paste this path and choose Open."
        >
          <ExtensionLoadPath compact hideHint />
        </SettingsRow>
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
    refetchInterval: () => pollMs(IDLE_POLL_MS),
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
      description="Reach this Mac’s Anteroom from your phone over your private tailnet; Funnel stays off."
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
