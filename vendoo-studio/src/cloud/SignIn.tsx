import React, { useState } from "react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { HOSTED_ORIGIN, useMacApp } from "../lib/macApp";
import { authErrorMessage, type AuthAction } from "./authMessages";

type Mode = "sign_in" | "create_account" | "reset";
const MIN_PASSWORD = 8;

/** Opens the reset form when the Mac app sends the seller here to reset in their browser. */
const RESET_PATH = "/?forgot-password";

export function SignIn({ client, notice, sandbox }: { client: SupabaseClient; notice?: string; sandbox?: boolean }) {
  const mac = useMacApp();
  const [mode, setMode] = useState<Mode>(() => new URLSearchParams(window.location.search).has("forgot-password") ? "reset" : "sign_in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState("");

  function switchTo(next: Mode) { setMode(next); setError(""); setSent(""); setPassword(""); }

  async function run(action: AuthAction, request: () => Promise<{ error: unknown }>, onDone?: () => void) {
    setPending(true); setError(""); setSent("");
    try {
      const { error } = await request();
      if (error) setError(authErrorMessage(action, error as { code?: string; message?: string; name?: string }));
      else onDone?.();
    } catch { setError(authErrorMessage(action, { name: "AuthRetryableFetchError" })); }
    finally { setPending(false); }
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mode === "sign_in") {
      void run("sign_in", () => client.auth.signInWithPassword({ email, password }));
    } else if (mode === "create_account") {
      void run("create_account", async () => {
        const result = await client.auth.signUp({ email, password, options: { emailRedirectTo: window.location.origin } });
        // With email confirmation on, Supabase answers without a session.
        if (!result.error && !result.data.session) {
          setSent(`We sent a confirmation link to ${email}. Open it, then sign in here with your password.`);
          setMode("sign_in"); setPassword("");
        }
        return result;
      });
    } else {
      void run("reset", () => client.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin }),
        () => setSent(`If ${email} has an account, we sent it a link to set a new password. Open it in this browser.`));
    }
  }

  const title = mode === "create_account" ? "Create your account" : mode === "reset" ? "Reset your password" : "Sign in";
  const button = mode === "create_account" ? "Create account" : mode === "reset" ? "Email me a reset link" : "Sign in";
  const busy = mode === "create_account" ? "Creating…" : mode === "reset" ? "Sending…" : "Signing in…";
  return (
    <main className="sign-in">
      <div className="pywebview-drag-region cloud-drag" aria-hidden="true" />
      <p className="eyebrow">YOUR LISTING WORKSPACE</p>
      <h1>{title}</h1>
      <p>{mode === "reset" ? "Enter your account’s email. We’ll send a link to choose a new password." : "One account for your listings on the web, iPhone and Mac."}</p>
      {sandbox && <p>Local sandbox: use alice@sandbox.test or bob@sandbox.test with the password <code>sandbox</code>.</p>}
      {notice && <p role="status">{notice}</p>}
      <form onSubmit={submit}>
        <label htmlFor="email">Email address</label>
        <input id="email" type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} disabled={pending} />
        {mode !== "reset" && <>
          <label htmlFor="password">Password</label>
          <input id="password" type="password" required minLength={mode === "create_account" ? MIN_PASSWORD : undefined}
            autoComplete={mode === "create_account" ? "new-password" : "current-password"}
            value={password} onChange={event => setPassword(event.target.value)} disabled={pending} />
          {mode === "create_account" && <p className="hint">At least {MIN_PASSWORD} characters.</p>}
        </>}
        <button className="primary" disabled={pending}>{pending ? busy : button}</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {sent && <p role="status">{sent}</p>}
      <nav className="sign-in-links" aria-label="Account">
        {mode === "sign_in" && <>
          <button type="button" className="link" onClick={() => switchTo("create_account")}>Create an account</button>
          {mac
            // A reset link opens in the default browser, which must be the one that asked for it.
            ? <a className="link" href={`${HOSTED_ORIGIN}${RESET_PATH}`} target="_blank" rel="noreferrer">Forgot password? Reset it in your browser</a>
            : <button type="button" className="link" onClick={() => switchTo("reset")}>Forgot password?</button>}
        </>}
        {mode !== "sign_in" && <button type="button" className="link" onClick={() => switchTo("sign_in")}>Back to sign in</button>}
      </nav>
      {mac && <p className="hint">To go back to this Mac’s listings, choose Account › This Mac’s Listings in the menu bar.</p>}
    </main>
  );
}

/** After a reset link, the seller chooses a new password before the workspace opens. */
export function SetPassword({ client, onDone }: { client: SupabaseClient; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setError("");
    try {
      const { error } = await client.auth.updateUser({ password });
      if (error) setError(authErrorMessage("new_password", error));
      else onDone();
    } catch { setError(authErrorMessage("new_password", { name: "AuthRetryableFetchError" })); }
    finally { setPending(false); }
  }
  return (
    <main className="sign-in">
      <div className="pywebview-drag-region cloud-drag" aria-hidden="true" />
      <p className="eyebrow">YOUR LISTING WORKSPACE</p>
      <h1>Choose a new password</h1>
      <form onSubmit={event => void submit(event)}>
        <label htmlFor="new-password">New password</label>
        <input id="new-password" type="password" autoComplete="new-password" required minLength={MIN_PASSWORD}
          value={password} onChange={event => setPassword(event.target.value)} disabled={pending} />
        <p className="hint">At least {MIN_PASSWORD} characters.</p>
        <button className="primary" disabled={pending}>{pending ? "Saving…" : "Save password"}</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
