import { useEffect, useState } from "react";
import { PlugIcon } from "lucide-react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { api } from "../api/client";
import type { McpTokenPermissions } from "../api/agentConnection";
import { Button } from "../components/ui/button";
import { Spinner } from "../components/ui/spinner";
import { NEW_TOKEN_PERMISSIONS, PermissionSelects } from "../components/settings/AgentSettings";
import { SettingsNote, SettingsRow, SettingsSection } from "../components/settings/SettingsLayout";
import { consentRequest } from "./appConsent";

type Details = { authorizationId: string; clientId: string; name: string; returnTo: string };

/**
 * Supabase's OAuth server sends the seller here when an app (Claude, ChatGPT,
 * Gemini) asks to use Anteroom's MCP tools. The seller chooses what the app may
 * do; Anteroom records that before Supabase gives the app its sign-in.
 */
export function AppConsentPage({ client, email }: { client: SupabaseClient; email: string }) {
  const [details, setDetails] = useState<Details | null>(null);
  const [permissions, setPermissions] = useState<McpTokenPermissions>(NEW_TOKEN_PERMISSIONS);
  const [authorizationId] = useState(() => consentRequest(window.location.search));
  const [error, setError] = useState(authorizationId ? "" : "This link is missing its request. Start connecting again from the app.");
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);

  useEffect(() => {
    if (!authorizationId) return;
    let active = true;
    void client.auth.oauth.getAuthorizationDetails(authorizationId).then(({ data, error: failed }) => {
      if (!active) return;
      if (failed || !data) { setError("This request expired or was already answered. Start connecting again from the app."); return; }
      // Approved before: Supabase answers with where to send the seller back.
      if (!("authorization_id" in data)) { window.location.assign(data.redirect_url); return; }
      let returnTo = data.redirect_uri;
      try { returnTo = new URL(data.redirect_uri).host; } catch { /* show it as given */ }
      setDetails({ authorizationId: data.authorization_id, clientId: data.client.id, name: data.client.name || "This app", returnTo });
    }).catch(() => { if (active) setError("Could not load this request. Check your connection and reload the page."); });
    return () => { active = false; };
  }, [client, authorizationId]);

  const answer = async (choice: "approve" | "deny") => {
    if (!details) return;
    setBusy(choice); setError("");
    try {
      if (choice === "approve") {
        const approval = { clientId: details.clientId, clientName: details.name, permissions };
        // What the app may reach is saved before it can sign in, but an app the
        // seller disconnected stays off until Supabase accepts this approval.
        await api.settings.mcpConnections.approve(approval);
        const { data, error: failed } = await client.auth.oauth.approveAuthorization(details.authorizationId, { skipBrowserRedirect: true });
        if (failed || !data) throw failed ?? new Error("No answer from sign-in");
        await api.settings.mcpConnections.approve({ ...approval, enable: true });
        window.location.assign(data.redirect_url);
      } else {
        const { error: failed } = await client.auth.oauth.denyAuthorization(details.authorizationId);
        if (failed) throw failed;
      }
    } catch {
      setError(choice === "approve" ? "Could not connect the app. Try again." : "Could not send your answer. Try again.");
      setBusy(null);
    }
  };

  return <div className="h-full overflow-y-auto"><main className="mx-auto grid max-w-lg gap-6 p-6">
    <header className="grid gap-2">
      <h1 className="flex items-center gap-2 text-xl font-medium"><PlugIcon className="size-5" />
        {details ? `Connect ${details.name} to Anteroom` : "Connect an app to Anteroom"}
      </h1>
      <p className="text-sm text-muted-foreground">Signed in as {email}</p>
    </header>
    {error ? <SettingsNote tone="error">{error}</SettingsNote> : null}
    {!details && !error ? <SettingsNote><Spinner /> Loading the request…</SettingsNote> : null}
    {details ? <>
      <SettingsSection title="What it can do">
        <SettingsRow title={details.name}
          description="Uses Anteroom's tools on your account as far as you allow here. It can never publish, update live listings or delist. You can change this or disconnect it in Settings → Agents.">
          <PermissionSelects permissions={permissions} labelPrefix={details.name} disabled={busy !== null}
            onChange={(area, level) => setPermissions(current => ({ ...current, [area]: level }))} />
        </SettingsRow>
      </SettingsSection>
      <p className="text-sm text-muted-foreground">After you answer, you go back to {details.returnTo}.</p>
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" disabled={busy !== null} onClick={() => { void answer("deny"); }}>
          {busy === "deny" ? <Spinner /> : null}Don't allow
        </Button>
        <Button disabled={busy !== null} onClick={() => { void answer("approve"); }}>
          {busy === "approve" ? <Spinner /> : null}Allow
        </Button>
      </div>
    </> : null}
  </main></div>;
}
