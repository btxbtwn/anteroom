import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ConnectionStatus } from "../api/contract.gen";
import { useWorkspaceRuntime } from "../app/runtime";
import { pollMs } from "../api/polling";
import { confirmDialog } from "../ui/confirmDialog";
import { Button } from "./ui/button";
import { FormError, Panel } from "./WorkspacePage";

const statusKey = ["plaid-status"];
const linkScriptUrl = "https://cdn.plaid.com/link/v2/stable/link-initialize.js";

interface LinkHandler {
  open: () => void;
  destroy: () => void;
}

interface PlaidLink {
  create: (options: {
    token: string;
    receivedRedirectUri?: string;
    onSuccess: (token: string, metadata: { institution?: { name?: string } }) => void;
    onExit: (error: unknown) => void;
  }) => LinkHandler;
}

let linkLoader: Promise<PlaidLink> | undefined;
function loadLink(): Promise<PlaidLink> {
  const browser = window as Window & { Plaid?: PlaidLink };
  if (browser.Plaid) return Promise.resolve(browser.Plaid);
  linkLoader ??= new Promise<PlaidLink>((resolve, reject) => {
    const script = document.createElement("script");
    const fail = () => {
      script.remove();
      reject(new Error("Plaid Link could not load. Check your connection and try again."));
    };
    const timeout = window.setTimeout(fail, 15_000);
    script.src = linkScriptUrl;
    script.async = true;
    script.onload = () => {
      window.clearTimeout(timeout);
      if (browser.Plaid) resolve(browser.Plaid);
      else fail();
    };
    script.onerror = () => { window.clearTimeout(timeout); fail(); };
    document.head.append(script);
  }).catch((error: unknown) => { linkLoader = undefined; throw error; });
  return linkLoader;
}

export function ConnectedBanks() {
  const queryClient = useQueryClient();
  const { account } = useWorkspaceRuntime();
  const resumeKey = account ? `anteroom-plaid-link:${account.id}` : null;
  const [linkError, setLinkError] = useState("");
  const [opening, setOpening] = useState(false);
  const activeLink = useRef<LinkHandler | null>(null);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      activeLink.current?.destroy();
      activeLink.current = null;
    };
  }, []);

  const status = useQuery({
    queryKey: statusKey,
    queryFn: ({ signal }) => api.plaid.status(signal),
    refetchInterval: () => pollMs(60_000),
  });
  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: statusKey }),
    queryClient.invalidateQueries({ queryKey: ["business-expenses"] }),
    queryClient.invalidateQueries({ queryKey: ["profit-and-loss"] }),
  ]);
  const exchange = useMutation({ mutationFn: api.plaid.exchange, onSettled: refresh });
  const mutateExchange = exchange.mutate;
  const openLink = useCallback(async (resume?: { token: string; url: string }) => {
    const clearResume = () => {
      if (resumeKey) sessionStorage.removeItem(resumeKey);
      if (resume) {
        const url = new URL(window.location.href);
        url.searchParams.delete("oauth_state_id");
        window.history.replaceState(window.history.state, "", url);
      }
    };
    const close = () => {
      const handler = activeLink.current;
      activeLink.current = null;
      handler?.destroy();
      if (mounted.current) setOpening(false);
    };
    try {
      const [token, link] = await Promise.all([
        resume ? Promise.resolve({ link_token: resume.token, redirect_uri: undefined }) : api.plaid.linkToken(),
        loadLink(),
      ]);
      if (!mounted.current) return;
      if (token.redirect_uri && resumeKey) {
        if (new URL(token.redirect_uri).origin !== window.location.origin) {
          throw new Error("Plaid's configured return address does not match this Anteroom site.");
        }
        sessionStorage.setItem(resumeKey, token.link_token);
      }
      activeLink.current = link.create({
        token: token.link_token,
        ...(resume && { receivedRedirectUri: resume.url }),
        onSuccess: (public_token, metadata) => {
          if (!mounted.current) return;
          clearResume();
          close();
          mutateExchange({ public_token, institution_name: metadata.institution?.name });
        },
        onExit: (error) => {
          if (!mounted.current) return;
          clearResume();
          close();
          if (error) setLinkError("Plaid Link closed before the institution connected. Try again.");
        },
      });
      activeLink.current.open();
    } catch (error) {
      close();
      if (mounted.current) setLinkError(error instanceof Error ? error.message : "Plaid Link could not open.");
    }
  }, [mutateExchange, resumeKey]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("oauth_state_id") || !resumeKey) return;
    // Wait through the development StrictMode effect replay before opening Link.
    const timer = window.setTimeout(() => {
      const token = sessionStorage.getItem(resumeKey);
      if (!token) {
        url.searchParams.delete("oauth_state_id");
        window.history.replaceState(window.history.state, "", url);
        setLinkError("Plaid could not resume this connection. Start again in the same browser tab.");
        return;
      }
      setOpening(true);
      void openLink({ token, url: url.href });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [openLink, resumeKey]);

  const connect = () => {
    setOpening(true);
    setLinkError("");
    exchange.reset();
    void openLink();
  };

  const data = status.data;
  const full = data && data.connections.length >= data.max_connections;
  const connecting = opening || exchange.isPending;
  return <Panel>
    <section aria-label="Connected banks and cards" className="space-y-2 p-3 sm:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium">Connected banks &amp; cards</h3>
          {data && <p className="text-xs tabular-nums text-muted-foreground">{data.connections.length}/{data.max_connections} connected</p>}
        </div>
        {data?.configured && <Button variant="outline" disabled={connecting || full} onClick={connect}>
          {exchange.isPending ? "Importing expenses…" : opening ? "Opening Plaid…" : "Connect bank or card"}
        </Button>}
      </div>
      {status.isPending && <p className="text-sm text-muted-foreground" role="status">Checking connections…</p>}
      {status.error && <FormError>{status.error.message}</FormError>}
      {data && !data.configured && <p className="text-sm text-muted-foreground">
        Bank sync is unavailable. Set PLAID_CLIENT_ID and PLAID_SECRET on the server to enable it.
      </p>}
      {data?.configured && data.connections.length === 0 && <p className="text-sm text-muted-foreground">
        Connect an institution to import up to two years of expenses.
      </p>}
      {full && <p className="text-sm text-muted-foreground">Disconnect an institution to connect another.</p>}
      {(linkError || exchange.error) && <FormError>{linkError || exchange.error?.message}</FormError>}
      {!!data?.connections.length && <ul className="divide-y divide-border/60">
        {data.connections.map((connection) => <BankConnection key={connection.id} connection={connection}
          configured={data.configured} refresh={refresh} />)}
      </ul>}
    </section>
  </Panel>;
}

function BankConnection({ connection, configured, refresh }: {
  connection: ConnectionStatus;
  configured: boolean;
  refresh: () => Promise<unknown>;
}) {
  const sync = useMutation({ mutationFn: () => api.plaid.sync(connection.id), onSettled: refresh });
  const disconnect = useMutation({ mutationFn: () => api.plaid.disconnect(connection.id), onSettled: refresh });
  const busy = sync.isPending || disconnect.isPending;
  const confirmDisconnect = async () => {
    if (await confirmDialog(`Disconnect ${connection.institution_name}?\nPreviously imported expenses will stay in your books.`,
      { variant: "destructive", confirmLabel: "Disconnect" })) disconnect.mutate();
  };
  return <li className="space-y-1 py-2">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0 break-words">
        <p className="text-sm font-medium">{connection.institution_name}</p>
        {connection.accounts.map((account, index) => <p key={index} className="text-xs text-muted-foreground">
          {account.name}{account.mask ? ` •••• ${account.mask}` : ""}
        </p>)}
        <p className="text-xs text-muted-foreground" role="status">
          {connection.status === "disconnecting" ? "Disconnect pending"
            : connection.status === "waiting" ? "Waiting for transaction history"
            : connection.last_synced_at ? `Last synced ${new Date(connection.last_synced_at).toLocaleString()}`
            : "No successful sync yet"}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={busy || !configured || connection.status === "disconnecting"}
          onClick={() => sync.mutate()}>{sync.isPending ? "Syncing…" : "Sync now"}</Button>
        <Button variant="ghost" disabled={busy} onClick={() => void confirmDisconnect()}>
          {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
        </Button>
      </div>
    </div>
    {(disconnect.error || sync.error || connection.last_error) &&
      <FormError>{disconnect.error?.message ?? sync.error?.message ?? connection.last_error}</FormError>}
  </li>;
}
