import { installErrorReports } from "../api/errorReports";
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createClient, type Session } from "@supabase/supabase-js";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { createStudioQueryClient } from "../api/queryClient";
import { installHostedApiSession, studioFetch } from "../api/transport";
import { forgetDeviceImages } from "../api/privateImages";
import { App } from "../app/App";
import { WorkspaceRuntimeProvider } from "../app/runtime";
import type { WorkspaceRuntime as HostedRuntime } from "../api/types";
import { AnchoredToastProvider, ToastProvider } from "../components/ui/toast";
import { AccountMenu } from "./AccountMenu";
import { cloudExtension } from "../api/cloudExtension";
import { applyTheme } from "../theme";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { installChunkReload } from "../lib/chunkReload";
import { installUnsavedWorkGuard } from "../lib/unsavedWork";
import { installSoftwareKeyboard } from "../lib/softwareKeyboard";
import { isMacApp, markMacApp, useMacApp } from "../lib/macApp";
import { SetPassword, SignIn } from "./SignIn";
import { Landing } from "./Landing";
import { SIGN_IN_PATH, showsLanding } from "./landingRoute";
import { linkOpenedElsewhere } from "./authMessages";
import { followSellerChanges } from "./liveUpdates";
import { StartupScreen } from "./StartupScreen";
import { ConnectorPage } from "./ConnectorPage";
import { DEVICE_BUSTER, DEVICE_MAX_AGE_MS, devicePersister, forgetDeviceWorkspace, keepReadsForDevice, keepsOnDevice, saveRuntime, savedRuntime } from "./deviceCache";
import "./workspace.css";

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
if (!url || !key) throw new Error("The cloud build requires Supabase public configuration.");
const supabase = createClient(url, key, { auth: { flowType: "pkce" } });
const connectorMode = new URLSearchParams(window.location.search).has("connector");
// A reset link signs in while the client starts, before React subscribes, so
// listen now: the seller must choose a new password before the workspace opens.
let recovering = false;
supabase.auth.onAuthStateChange(event => { if (event === "PASSWORD_RECOVERY") recovering = true; });
// A link from another browser or the Mac app carries a code this browser cannot use.
const openedElsewhere = linkOpenedElsewhere(window.location.search);
const sandbox = import.meta.env.DEV && import.meta.env.VITE_ANTEROOM_SANDBOX === "1" && new URL(url).hostname === "127.0.0.1";
installErrorReports();
applyTheme();
installSoftwareKeyboard(window);
markMacApp();
installChunkReload();
installUnsavedWorkGuard(window);

function CloudApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [newPassword, setNewPassword] = useState(false);
  const mac = useMacApp();
  useEffect(() => {
    const connect = () => {
      const native = (window as unknown as { pywebview?: { api?: { connect_chatgpt_worker?: (token: string | null, owner: string | null) => Promise<unknown> } } }).pywebview?.api;
      void native?.connect_chatgpt_worker?.(session?.access_token ?? null, session?.user.id ?? null)?.catch(() => {});
    };
    connect();
    window.addEventListener("pywebviewready", connect);
    return () => window.removeEventListener("pywebviewready", connect);
  }, [session?.access_token]);
  useEffect(() => {
    let active = true;
    let received = false;
    let previous: string | undefined;
    const accept = (value: Session | null) => {
      if (!active) return;
      if (previous && previous !== value?.user.id) {
        // The desktop workspace also has module-level stores. A new document
        // clears all of them before another account can render.
        // Let the Bridge revoke remembered approval before navigation unloads
        // the content script and can discard the disconnect message. Signing out
        // returns to the sign-in form, not the landing page.
        // The last seller's thumbnails and workspace kept on this device go with them.
        void Promise.allSettled([cloudExtension({ id: previous, email: "" }).disconnect(), forgetDeviceImages(), forgetDeviceWorkspace()]).finally(() => {
          window.location.replace(new URL(connectorMode ? "/?connector&sign-in" : SIGN_IN_PATH, window.location.origin).href);
        });
        return;
      }
      previous = value?.user.id;
      if (recovering && value) { recovering = false; setNewPassword(true); }
      setSession(value); setReady(true);
    };
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, value) => { received = true; accept(value); });
    void supabase.auth.getSession().then(({ data, error }) => {
      if (openedElsewhere) window.history.replaceState(null, "", window.location.pathname);
      if (openedElsewhere && !data.session && active) setError("That email link opened in a different browser or app than the one that asked for it. If you just confirmed your email, sign in with your password.");
      else if (error && active) setError("Could not restore your session. Please sign in again.");
      if (!received) accept(data.session);
    }).catch(() => { if (active) { setError("Could not restore your session. Please sign in again."); setReady(true); } });
    return () => { active = false; subscription.unsubscribe(); };
  }, []);
  if (!ready) return <StartupScreen/>;
  if (session && newPassword) return <div className="cloud-auth"><SetPassword client={supabase} onDone={() => setNewPassword(false)}/></div>;
  if (session && connectorMode) return <ConnectorPage email={session.user.email || "Your Anteroom account"} onSignOut={async () => {
    const { error } = await supabase.auth.signOut({ scope: "local" });
    if (error) throw error;
  }}/>;
  if (session) return <HostedWorkspace key={session.user.id} session={session}/>;
  if (!error && showsLanding(window.location.search, mac)) return <Landing/>;
  return <div className="cloud-auth"><SignIn client={supabase} notice={error} sandbox={sandbox}/></div>;
}

function HostedWorkspace({ session }: { session: Session }) {
  const [queryClient] = useState(() => {
    const client = createStudioQueryClient();
    keepReadsForDevice(client);
    return client;
  });
  const [persistOptions] = useState(() => ({
    persister: devicePersister(session.user.id),
    maxAge: DEVICE_MAX_AGE_MS,
    buster: DEVICE_BUSTER,
    dehydrateOptions: { shouldDehydrateQuery: keepsOnDevice },
  }));
  const [runtime, setRuntime] = useState<HostedRuntime | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const refresh = { active: true, fresh: false, shown: false };
    // Open on the workspace saved from the last visit while the server confirms it.
    void savedRuntime<HostedRuntime>(session.user.id).then(saved => {
      if (refresh.active && saved && !refresh.fresh) { refresh.shown = true; setRuntime(saved); }
    });
    const remove = installHostedApiSession({
      origin: window.location.origin,
      seller: session.user.id,
      accessToken: async () => {
        const { data, error } = await supabase.auth.getSession();
        if (error || data.session?.user.id !== session.user.id) return null;
        return data.session.access_token;
      },
      unauthorized: () => { void supabase.auth.signOut({ scope: "local" }); },
    });
    void studioFetch("/api/runtime").then(async response => {
      if (!response.ok) throw new Error("Could not open your workspace. Reload to try again.");
      const value = await response.json();
      refresh.fresh = true;
      if (refresh.active) setRuntime(value);
      void saveRuntime(session.user.id, value);
    // Once the saved workspace is open, each screen reports its own failed refresh.
    }).catch(error => { if (refresh.active && !refresh.shown) setError(error.message); });
    return () => { refresh.active = false; remove(); queryClient.clear(); };
  }, [session.user.id, session.user.email, queryClient]);
  // Changes from the seller's other devices arrive live; polling covers any gap.
  // supabase-js refreshes the Realtime token itself, so a refresh need not resubscribe.
  const [liveToken] = useState(session.access_token);
  useEffect(() => followSellerChanges(supabase, session.user.id, liveToken, queryClient),
    [session.user.id, liveToken, queryClient]);
  // The Mac app's window has no Chrome extension; its own Chrome connection stays with this Mac's listings.
  const controls = <div className="cloud-account"><AccountMenu account={{ id: session.user.id, email: session.user.email || "" }} chrome={!isMacApp()} canDelete={!!runtime?.features.includes("account_deletion")} onSignOut={() => void supabase.auth.signOut({ scope: "local" }).then(({ error }) => { if (error) setError("Could not sign out. Please try again."); })}/></div>;
  if (!runtime) return <StartupScreen error={error}/>;
  return <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(runtime.features), views: new Set(runtime.views), marketplaceForms: new Set(runtime.marketplace_forms), actionMarketplaces: new Set(runtime.action_marketplaces), accountControls: controls }}>
    <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}><ToastProvider><AnchoredToastProvider>
      {error && <div role="alert">{error}</div>}<App/>
    </AnchoredToastProvider></ToastProvider></PersistQueryClientProvider>
  </WorkspaceRuntimeProvider>;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode><ErrorBoundary label="Anteroom" className="h-dvh"><CloudApp/></ErrorBoundary></React.StrictMode>,
);
