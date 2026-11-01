import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createClient, type Session } from "@supabase/supabase-js";
import { QueryClientProvider } from "@tanstack/react-query";
import { createStudioQueryClient } from "../api/queryClient";
import { installHostedApiSession, studioFetch } from "../api/transport";
import { App } from "../app/App";
import { WorkspaceRuntimeProvider, type WorkspaceFeature } from "../app/runtime";
import { AnchoredToastProvider, ToastProvider } from "../components/ui/toast";
import { ChromeConnection } from "./ChromeConnection";
import { cloudExtension } from "../api/cloudExtension";
import { applyTheme } from "../theme";
import { installSoftwareKeyboard } from "../lib/softwareKeyboard";
import { isMacApp, markMacApp } from "../lib/macApp";
import { SetPassword, SignIn } from "./SignIn";
import { linkOpenedElsewhere } from "./authMessages";
import "../styles/app.css";
import "./workspace.css";

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
if (!url || !key) throw new Error("The cloud build requires Supabase public configuration.");
const supabase = createClient(url, key, { auth: { flowType: "pkce" } });
// A reset link signs in while the client starts, before React subscribes, so
// listen now: the seller must choose a new password before the workspace opens.
let recovering = false;
supabase.auth.onAuthStateChange(event => { if (event === "PASSWORD_RECOVERY") recovering = true; });
// A link from another browser or the Mac app carries a code this browser cannot use.
const openedElsewhere = linkOpenedElsewhere(window.location.search);
const sandbox = import.meta.env.DEV && import.meta.env.VITE_ANTEROOM_SANDBOX === "1" && new URL(url).hostname === "127.0.0.1";
applyTheme();
installSoftwareKeyboard(window);
markMacApp();

function CloudApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [newPassword, setNewPassword] = useState(false);
  useEffect(() => {
    let active = true;
    let received = false;
    let previous: string | undefined;
    const accept = (value: Session | null) => {
      if (!active) return;
      if (previous && previous !== value?.user.id) {
        // The desktop workspace also has module-level stores. A new document
        // clears all of them before another account can render.
        void cloudExtension({ id: previous, email: "" }).disconnect();
        window.location.replace(window.location.origin);
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
  if (!ready) return <main className="cloud-auth">Loading…</main>;
  if (session && newPassword) return <div className="cloud-auth"><SetPassword client={supabase} onDone={() => setNewPassword(false)}/></div>;
  if (session) return <HostedWorkspace key={session.user.id} session={session}/>;
  return <div className="cloud-auth"><SignIn client={supabase} notice={error} sandbox={sandbox}/></div>;
}

function HostedWorkspace({ session }: { session: Session }) {
  const [queryClient] = useState(createStudioQueryClient);
  const [runtime, setRuntime] = useState<{ features: WorkspaceFeature[]; views: string[]; marketplace_forms: string[] } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const remove = installHostedApiSession({
      origin: window.location.origin,
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
      if (active) setRuntime(value);
    }).catch(error => { if (active) setError(error.message); });
    return () => { active = false; remove(); queryClient.clear(); };
  }, [session.user.id, session.user.email, queryClient]);
  // The Mac app's window has no Chrome extension; its own Chrome connection stays with this Mac's listings.
  const controls = <div className="cloud-account">{!isMacApp() && <ChromeConnection account={{ id: session.user.id, email: session.user.email || "" }}/>}<button type="button" onClick={() => void supabase.auth.signOut({ scope: "local" }).then(({ error }) => { if (error) setError("Could not sign out. Please try again."); })}>Sign out</button></div>;
  if (!runtime) return <main className="cloud-auth" role={error ? "alert" : "status"}>{error || "Opening your workspace…"}</main>;
  return <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(runtime.features), views: new Set(runtime.views), marketplaceForms: new Set(runtime.marketplace_forms), accountControls: controls }}>
    <QueryClientProvider client={queryClient}><ToastProvider><AnchoredToastProvider>
      {error && <div role="alert">{error}</div>}<App/>
    </AnchoredToastProvider></ToastProvider></QueryClientProvider>
  </WorkspaceRuntimeProvider>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><CloudApp/></React.StrictMode>);
