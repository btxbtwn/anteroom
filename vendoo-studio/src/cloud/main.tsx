import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";
import { cloudDrafts, type CloudDraft } from "../api/cloudDrafts";
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
  async function signOut() {
    if (dirty && !window.confirm("Sign out and discard unsaved changes?")) return;
    const { error } = await supabase.auth.signOut({ scope: "local" });
    if (error) setError("Could not sign out. Please try again.");
  }
  return <div className="cloud"><header><a href="/" aria-label="Anteroom home">Anteroom</a>{session && <div><span>{session.user.email}</span><button onClick={() => void signOut()}>Sign out</button></div>}</header>
    {error && <p className="message" role="alert">{error}</p>}
    {!ready ? <main><p>Loading…</p></main> : session ? <Workspace key={session.user.id} client={supabase} dirty={dirty} onDirtyChange={setDirty}/> : <SignIn client={supabase}/>}
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

function Workspace({ client, dirty, onDirtyChange: setDirty }: { client: SupabaseClient; dirty: boolean; onDirtyChange: (dirty: boolean) => void }) {
  const [api] = useState(() => cloudDrafts(client));
  const [rows, setRows] = useState<CloudDraft[]>([]);
  const [selected, setSelected] = useState<CloudDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void api.list().then(data => { if (active) setRows(data); }).catch(error => { if (active) setError(error.message); });
    return () => { active = false; };
  }, [api]);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);
  const leave = () => !dirty || window.confirm("Discard unsaved changes?");
  const saved = (draft: CloudDraft) => { setRows(previous => [draft, ...previous.filter(row => row.id !== draft.id)]); setSelected(draft); setDirty(false); };
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
  return <main className="workspace"><aside><div className="list-heading"><h1>Recent drafts</h1><button className="primary" disabled={busy} onClick={() => void create()}>New draft</button></div>
    {rows.length === 0 && <p>Your next listing starts here.</p>}
    <nav aria-label="Drafts">{rows.map(row => <button key={row.id} aria-current={selected?.id === row.id ? "page" : undefined} disabled={busy} onClick={() => void open(row.id)}>{row.title || "Untitled draft"}</button>)}</nav></aside>
    <section>{error && <p role="alert" className="message">{error}</p>}{selected ? <Editor key={`${selected.id}:${selected.updated_at}`} draft={selected} api={api} onSaved={saved} onDirty={setDirty} onBusy={setBusy}/> : <div className="empty"><h2>Room for your next idea.</h2><p>Create a draft to save a title and notes. Your work is private to your account.</p></div>}</section>
  </main>;
}

function Editor({ draft, api, onSaved, onDirty, onBusy }: { draft: CloudDraft; api: ReturnType<typeof cloudDrafts>; onSaved: (draft: CloudDraft) => void; onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void }) {
  const [title, setTitle] = useState(draft.title || "");
  const [notes, setNotes] = useState(draft.notes || "");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const dirty = title !== (draft.title || "") || notes !== (draft.notes || "");
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) { setMessage("Enter a title."); return; }
    setSaving(true); onBusy(true); setMessage("");
    try { onSaved(await api.save(draft, title, notes)); }
    catch (error) { setMessage((error as Error).message); setConflict((error as { status?: number }).status === 409); }
    finally { setSaving(false); onBusy(false); }
  }
  async function reload() {
    if (dirty && !window.confirm("Reload this draft and discard unsaved changes?")) return;
    setSaving(true); onBusy(true);
    try { onSaved(await api.get(draft.id)); } catch (error) { setMessage((error as Error).message); }
    finally { setSaving(false); onBusy(false); }
  }
  return <form className="editor" onSubmit={event => void submit(event)}><p className="eyebrow">PRIVATE DRAFT</p><label htmlFor="title">Title</label><input id="title" required maxLength={200} value={title} onChange={event => setTitle(event.target.value)} disabled={saving}/>
    <label htmlFor="notes">Notes</label><textarea id="notes" maxLength={20000} rows={12} value={notes} onChange={event => setNotes(event.target.value)} disabled={saving}/>
    <div className="editor-actions"><button className="primary" disabled={saving || !dirty || conflict}>{saving ? "Saving…" : "Save draft"}</button><button type="button" disabled={saving} onClick={() => void reload()}>Reload draft</button><span role="status">{dirty ? "Unsaved changes" : "Saved"}</span></div>
    {message && <p role="alert" className="message">{message}</p>}
  </form>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
