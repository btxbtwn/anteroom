import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LinkIcon, PauseIcon, PlayIcon } from "lucide-react";
import { Button } from "../components/ui/button";
import { SettingsNote, SettingsRow, SettingsSection, SettingsSelect } from "../components/settings/SettingsLayout";
import { connectorBridge, nativeClaudeTerminal, type ConnectorStatus } from "./connectorBridge";

const ClaudeLoginTerminal = lazy(() => import("../components/ClaudeLoginTerminal").then(module => ({ default: module.ClaudeLoginTerminal })));
const DOWNLOAD = "https://github.com/btxbtwn/anteroom/releases/download/studio-macos/Anteroom-Connector-macos.zip";
const CONNECTION_LABELS: Record<string, string> = {
  connected: "Connected to Anteroom", connecting: "Connecting to Anteroom", offline: "Waiting for a network connection",
  in_use: "Another desktop app or connector is connected to this account", signed_out: "Connecting your account",
  sign_in_required: "Sign out and reconnect your Anteroom account",
};

export function ConnectorPage({ email, onSignOut }: { email: string; onSignOut: () => Promise<void> }) {
  const [bridge, setBridge] = useState(connectorBridge);
  const [status, setStatus] = useState<ConnectorStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [models, setModels] = useState<Record<string, { value: string; label: string }[]>>({});
  const [claudeOpen, setClaudeOpen] = useState(false);
  const version = useRef(0);
  const terminal = useMemo(() => bridge ? nativeClaudeTerminal(bridge) : undefined, [bridge]);
  useEffect(() => {
    const ready = () => setBridge(connectorBridge());
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
  const refresh = useCallback(async () => {
    if (!bridge) return;
    const request = ++version.current;
    const next = await bridge.connector_status();
    if (request === version.current) setStatus(next);
  }, [bridge]);
  useEffect(() => {
    const polling: { active: boolean; timer?: ReturnType<typeof setTimeout> } = { active: true };
    const poll = async () => {
      try { await refresh(); } catch { if (polling.active) setError("Could not read the connector. Reopen its connection window to try again."); }
      if (polling.active) polling.timer = setTimeout(() => { void poll(); }, 3000);
    };
    if (bridge) void poll();
    return () => { polling.active = false; version.current++; clearTimeout(polling.timer); };
  }, [bridge, refresh]);
  const run = async (name: string, action: () => Promise<unknown>) => {
    setBusy(current => ({ ...current, [name]: true })); setError("");
    try { await action(); await refresh(); }
    catch { setError("Could not complete that action. Check your connection and try again."); }
    finally { setBusy(current => ({ ...current, [name]: false })); }
  };
  if (!bridge) return <main className="mx-auto grid max-w-lg gap-4 p-6">
    <h1 className="text-xl font-medium">Anteroom Connector</h1>
    <p>Open this page in Anteroom Connector to connect your subscriptions.</p>
    <a href={DOWNLOAD}>Download for Mac</a>
  </main>;
  return <main className="mx-auto grid max-w-2xl gap-6 p-6">
    <header className="grid gap-2">
      <h1 className="flex items-center gap-2 text-xl font-medium"><LinkIcon className="size-5" />Anteroom Connector</h1>
      <p className="text-sm text-muted-foreground">Use your subscriptions from the web and iPhone.</p>
    </header>
    <SettingsSection title="Connection">
      <SettingsRow title={email} description={status?.paused ? "Paused. Current requests can finish; new requests are stopped." : CONNECTION_LABELS[status?.connection ?? "connecting"] ?? "Connecting to Anteroom"}
        control={<Button size="sm" variant="outline" disabled={!status || busy.pause} onClick={() => { void run("pause", () => bridge.connector_pause(!status?.paused)); }}>
          {status?.paused ? <PlayIcon /> : <PauseIcon />}{status?.paused ? "Resume" : "Pause"}
        </Button>} />
      {Boolean(status?.active_calls) && <SettingsNote>{status?.active_calls} active requests</SettingsNote>}
    </SettingsSection>
    <SettingsSection title="Subscriptions">
      {(["chatgpt", "claude"] as const).map(provider => {
        const label = provider === "chatgpt" ? "ChatGPT" : "Claude";
        const state = status?.[provider];
        const installed = provider !== "claude" || state?.installed;
        return <div className="grid gap-3" key={provider}>
          <SettingsRow title={label} description={state?.signed_in ? "Signed in with your subscription" : !installed ? "Install Claude Code, then connect your subscription." : state?.pending ? "Finish signing in in your browser." : "Connect your subscription on this Mac."}
            control={state?.signed_in ? provider === "chatgpt" ? <Button size="sm" variant="ghost" disabled={busy.chatgpt} onClick={() => { void run(provider, bridge.connector_chatgpt_logout); }}>Sign out</Button> : null
              : <Button size="sm" variant="outline" disabled={!status || busy[provider] || claudeOpen && provider === "claude"} onClick={() => {
                if (provider === "claude" && installed) setClaudeOpen(true);
                else void run(provider, provider === "chatgpt" ? bridge.connector_chatgpt_login : bridge.connector_setup_claude);
              }}>{installed ? state?.pending ? "Open sign-in again" : "Sign in" : "Set up Claude Code"}</Button>} />
          {state?.error && <p role="alert" className="px-4 text-sm text-destructive-foreground">{state.error}</p>}
          {state?.signed_in && <SettingsRow title={`${label} model`} description="Used for listing text and photos."
            control={<div className="flex items-center gap-2">
              <SettingsSelect aria-label={`${label} model`} value={state.listing_model} disabled={busy[provider]} onChange={model => { void run(provider, () => bridge.connector_set_model(provider, model)); }}
                options={models[provider]?.length ? models[provider] : [{ value: state.listing_model, label: state.listing_model }]} />
              <Button size="sm" variant="ghost" disabled={busy[provider]} onClick={() => { void run(provider, async () => {
                const items = await bridge.connector_models(provider);
                setModels(current => ({ ...current, [provider]: items }));
              }); }}>Load models</Button>
            </div>} />}
        </div>;
      })}
      {claudeOpen && <div className="p-4"><Suspense fallback={<p>Opening Claude sign-in…</p>}>
        <ClaudeLoginTerminal openTerminal={terminal} onClose={() => { setClaudeOpen(false); void refresh(); }} />
      </Suspense></div>}
    </SettingsSection>
    <SettingsNote>Keep this Mac awake, online, and the connector running. Closing this window keeps it in the menu bar. Subscription credentials stay on this Mac.</SettingsNote>
    {error && <p role="alert" className="text-sm text-destructive-foreground">{error}</p>}
    <Button variant="ghost" size="sm" disabled={busy.account} onClick={() => { void run("account", onSignOut); }}>Disconnect Anteroom account</Button>
  </main>;
}
