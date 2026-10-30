import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { cloudDrafts, type CloudDraft } from "../api/cloudDrafts";
import { GeneralFields, type GeneralValues } from "./GeneralFields";
import { Photos } from "./Photos";
import { MarketplacePreview } from "./MarketplacePreview";
import { ChromeConnection } from "./ChromeConnection";
import { cloudExtension } from "../api/cloudExtension";
import "./style.css";

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
if (!url || !key) throw new Error("The cloud build requires Supabase public configuration.");
const supabase = createClient(url, key, { auth: { flowType: "pkce" } });

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    let active = true;
    let received = false;
    let lastUser: string | undefined;
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, value) => {
      if (active) { received = true; if (lastUser !== value?.user.id) setDirty(false); lastUser = value?.user.id; setSession(value); setReady(true); }
    });
    void supabase.auth.getSession().then(({ data, error }) => {
      if (active) {
        if (error) setError("Could not restore your session. Please sign in again.");
        if (!received) { setSession(data.session); setReady(true); }
      }
    }).catch(() => { if (active) { setError("Could not restore your session. Please sign in again."); setReady(true); } });
    return () => { active = false; subscription.unsubscribe(); };
  }, []);
  const previousAccount = useRef<string | null>(null);
  useEffect(() => {
    const previous = previousAccount.current;
    previousAccount.current = session?.user.id || null;
    if (previous && previous !== session?.user.id) {
      void cloudExtension({ id: previous, email: "" }).disconnect();
    }
  }, [session?.user.id]);
  async function signOut() {
    if (dirty && !window.confirm("Sign out and discard unsaved changes?")) return;
    const { error } = await supabase.auth.signOut({ scope: "local" });
    if (error) setError("Could not sign out. Please try again.");
  }
  return <div className="cloud"><header><a href="/" aria-label="Anteroom home">Anteroom</a>{session && <div><span>{session.user.email}</span><ChromeConnection key={session.user.id} account={{ id: session.user.id, email: session.user.email || "" }}/><button onClick={() => void signOut()}>Sign out</button></div>}</header>
    {error && <p className="message" role="alert">{error}</p>}
    {!ready ? <main><p>Loading…</p></main> : session ? <Workspace key={session.user.id} client={supabase} account={{ id: session.user.id, email: session.user.email || "" }} dirty={dirty} onDirtyChange={setDirty}/> : <SignIn client={supabase}/>}
  </div>;
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

function Workspace({ client, account, dirty, onDirtyChange: setDirty }: { client: SupabaseClient; account: { id: string; email: string }; dirty: boolean; onDirtyChange: (dirty: boolean) => void }) {
  const [api] = useState(() => cloudDrafts(client));
  const [query, setQuery] = useState("");
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<CloudDraft[]>([]);
  const [selected, setSelected] = useState<CloudDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    const timer = window.setTimeout(() => {
      void api.list(0, query).then(data => { if (active) { setRows(data); setMore(data.length === 100); setError(""); } }).catch(error => { if (active) setError(error.message); }).finally(() => { if (active) setLoading(false); });
    }, 200);
    return () => { active = false; window.clearTimeout(timer); };
  }, [api, query]);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);
  const leave = () => !dirty || window.confirm("Discard unsaved changes?");
  const saved = (draft: CloudDraft) => { setRows(previous => [...(draft.title?.toLowerCase().includes(query.toLowerCase()) ? [draft] : []), ...previous.filter(row => row.id !== draft.id)]); setSelected(draft); setDirty(false); };
  async function open(id: string) {
    if (!leave()) return;
    setBusy(true); setError("");
    try { setSelected(await api.get(id)); setDirty(false); } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  async function create() {
    if (!leave()) return;
    setBusy(true); setError("");
    try { saved(await api.create("Untitled draft")); } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  }
  async function loadMore() {
    setBusy(true); setError("");
    try { const data = await api.list(rows.length, query); setRows(previous => [...previous, ...data.filter(row => !previous.some(existing => existing.id === row.id))]); setMore(data.length === 100); }
    catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }
  return <main className="workspace"><aside><div className="list-heading"><h1>Inventory</h1><button className="primary" disabled={busy} onClick={() => void create()}>New draft</button></div>
    <label htmlFor="inventory-search" className="search-label">Search listings</label><input id="inventory-search" type="search" placeholder="Search listings" value={query} maxLength={200} disabled={busy} onChange={event => setQuery(event.target.value)}/>
    <p className="eyebrow">DRAFTS · {rows.length}{more ? "+" : ""}</p>
    {loading && <p role="status">Loading inventory…</p>}
    {!loading && rows.length === 0 && <p>Your next listing starts here.</p>}
    <nav aria-label="Drafts">{rows.map(row => <button key={row.id} aria-current={selected?.id === row.id ? "page" : undefined} disabled={busy} onClick={() => void open(row.id)}><span>{row.title || "Untitled draft"}</span><small>Draft</small></button>)}</nav>{more && <button disabled={busy || loading} onClick={() => void loadMore()}>Load more listings</button>}</aside>
    <section>{error && <p role="alert" className="message">{error}</p>}{selected ? <Editor key={`${selected.id}:${selected.updated_at}`} draft={selected} api={api} client={client} account={account} onSaved={saved} onDirty={setDirty} onBusy={setBusy}/> : <div className="empty"><h2>Room for your next idea.</h2><p>Create a listing to add photos, edit its fields, and save your draft. Your work is private to your account.</p></div>}</section>
  </main>;
}

function Editor({ draft, api, client, account, onSaved, onDirty, onBusy }: { draft: CloudDraft; client: SupabaseClient; account: { id: string; email: string }; api: ReturnType<typeof cloudDrafts>; onSaved: (draft: CloudDraft) => void; onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void }) {
  const [tab, setTab] = useState<"general" | "poshmark">("general");
  const [fields, setFields] = useState<GeneralValues>(draft.fields || {});
  const [photoBusy, setPhotoBusy] = useState(false);
  const [title, setTitle] = useState(draft.title || "");
  const [notes, setNotes] = useState(draft.notes || "");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const dirty = title !== (draft.title || "") || notes !== (draft.notes || "") || JSON.stringify(fields) !== JSON.stringify(draft.fields || {});
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) { setMessage("Enter a title."); return; }
    setSaving(true); onBusy(true); setMessage("");
    try { onSaved(await api.save(draft, title, notes, fields)); }
    catch (error) { setMessage((error as Error).message); setConflict((error as { status?: number }).status === 409); }
    finally { setSaving(false); onBusy(false); }
  }
  async function reload() {
    if (dirty && !window.confirm("Reload this draft and discard unsaved changes?")) return;
    setSaving(true); onBusy(true);
    try { onSaved(await api.get(draft.id)); } catch (error) { setMessage((error as Error).message); }
    finally { setSaving(false); onBusy(false); }
  }
  return <form className="editor" onSubmit={event => void submit(event)}><div className="editor-tabs" role="tablist" aria-label="Listing forms"><button type="button" role="tab" aria-selected={tab === "general"} disabled={saving || photoBusy} onClick={() => setTab("general")}>General</button><button type="button" role="tab" aria-selected={tab === "poshmark"} disabled={saving || photoBusy} onClick={() => setTab("poshmark")}>Poshmark preview</button></div>
    {tab === "poshmark" ? <MarketplacePreview key={draft.id} account={account} listing={{ ...fields, title }} client={client} draftId={draft.id}/> : <><p className="eyebrow">INVENTORY / GENERAL</p><Photos client={client} draftId={draft.id} disabled={saving || photoBusy} onBusy={value => { setPhotoBusy(value); onBusy(value); }}/><label htmlFor="title">Title</label><input id="title" required maxLength={200} value={title} onChange={event => setTitle(event.target.value)} disabled={saving || photoBusy}/>
    <GeneralFields values={fields} disabled={saving || photoBusy} onChange={(key, value) => setFields(previous => ({ ...previous, [key]: value }))}/>
    <label htmlFor="notes">Notes</label><textarea id="notes" maxLength={20000} rows={12} value={notes} onChange={event => setNotes(event.target.value)} disabled={saving || photoBusy}/>
    <div className="editor-actions"><button className="primary" disabled={saving || photoBusy || !dirty || conflict}>{saving ? "Saving…" : "Save draft"}</button><button type="button" disabled={saving || photoBusy} onClick={() => void reload()}>Reload draft</button><span role="status">{dirty ? "Unsaved changes" : "Saved"}</span></div>
    {message && <p role="alert" className="message">{message}</p>}</>}
  </form>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
