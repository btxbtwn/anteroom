import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { QueryClientProvider } from "@tanstack/react-query";
import { createStudioQueryClient } from "../api/queryClient";
import { installHostedApiSession, studioFetch } from "../api/transport";
import { installHostedBrowser } from "../api/hostedBrowser";
import { api } from "../api/client";
import { App } from "../app/App";
import { WorkspaceRuntimeProvider, type WorkspaceFeature } from "../app/runtime";
import { AnchoredToastProvider, ToastProvider } from "../components/ui/toast";
import { ChromeConnection } from "./ChromeConnection";
import { cloudExtension } from "../api/cloudExtension";
import { applyTheme } from "../theme";
import { installSoftwareKeyboard } from "../lib/softwareKeyboard";
import "../styles/app.css";
import "./workspace.css";

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
if (!url || !key) throw new Error("The cloud build requires Supabase public configuration.");
const supabase = createClient(url, key, { auth: { flowType: "pkce" } });
applyTheme();
installSoftwareKeyboard(window);

function CloudApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
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
      setSession(value); setReady(true);
    };
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, value) => { received = true; accept(value); });
    void supabase.auth.getSession().then(({ data, error }) => {
      if (error && active) setError("Could not restore your session. Please sign in again.");
      if (!received) accept(data.session);
    }).catch(() => { if (active) { setError("Could not restore your session. Please sign in again."); setReady(true); } });
    return () => { active = false; subscription.unsubscribe(); };
  }, []);
  if (!ready) return <main className="cloud-auth">Loading…</main>;
  if (session) return <HostedWorkspace key={session.user.id} session={session}/>;
  return <div className="cloud-auth">{error && <p role="alert">{error}</p>}<SignIn client={supabase}/></div>;
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
    const bridge = cloudExtension({ id: session.user.id, email: session.user.email || "" });
    const removeBrowser = installHostedBrowser({ form: async (id, marketplace, revisionId) => {
      if (marketplace !== "poshmark") throw new Error("This marketplace is still being connected to the hosted workspace.");
      const saved = await api.listings.get(id);
      if (revisionId && saved.current_revision_id !== revisionId) throw new Error("The listing changed. Reload its form.");
      // Bridge's reviewed read contract accepts listing copy, never sessions,
      // database identities, paths or operational marketplace identifiers.
      const copy: Record<string, string | number | null> = {};
      for (const name of ["title", "description", "price", "brand", "size", "condition", "primaryColor", "secondaryColor", "category_path", "sku", "zipCode", "department", "quantity", "cost", "weight_oz", "package_dimensions_in"]) {
        const value = saved.listing[name];
        if (typeof value === "string" || typeof value === "number") copy[name] = value;
      }
      return bridge.readPoshmarkForm(copy);
    } });
    void studioFetch("/api/runtime").then(async response => {
      if (!response.ok) throw new Error("Could not open your workspace. Reload to try again.");
      const value = await response.json();
      if (active) setRuntime(value);
    }).catch(error => { if (active) setError(error.message); });
    return () => { active = false; removeBrowser(); remove(); queryClient.clear(); };
  }, [session.user.id, session.user.email, queryClient]);
  const controls = <div className="cloud-account"><ChromeConnection account={{ id: session.user.id, email: session.user.email || "" }}/><button type="button" onClick={() => void supabase.auth.signOut({ scope: "local" }).then(({ error }) => { if (error) setError("Could not sign out. Please try again."); })}>Sign out</button></div>;
  if (!runtime) return <main className="cloud-auth" role={error ? "alert" : "status"}>{error || "Opening your workspace…"}</main>;
  return <WorkspaceRuntimeProvider value={{ hosted: true, features: new Set(runtime.features), views: new Set(runtime.views), marketplaceForms: new Set(runtime.marketplace_forms), accountControls: controls }}>
    <QueryClientProvider client={queryClient}><ToastProvider><AnchoredToastProvider>
      {error && <div role="alert">{error}</div>}<App/>
    </AnchoredToastProvider></ToastProvider></QueryClientProvider>
  </WorkspaceRuntimeProvider>;
}

function SignIn({ client }: { client: SupabaseClient }) {
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setMessage("");
    try {
      const { error } = await client.auth.signInWithOtp({ email, options: { emailRedirectTo: window.location.origin } });
      setMessage(error ? "Could not send the sign-in link. Please wait a minute and try again." : "Check your email for a sign-in link. Open it in this browser to continue.");
    } catch { setMessage("Could not reach sign-in. Please try again."); }
    finally { setPending(false); }
  }
  return <main className="sign-in"><p className="eyebrow">YOUR LISTING WORKSPACE</p><h1>A place to start.</h1><p>Sign in to keep your drafts together, wherever you work.</p>
    <form onSubmit={event => void submit(event)}><label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} disabled={pending}/><button className="primary" disabled={pending}>{pending ? "Sending…" : "Email me a sign-in link"}</button></form>
    {message && <p role="status">{message}</p>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><CloudApp/></React.StrictMode>);
