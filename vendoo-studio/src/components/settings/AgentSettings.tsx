import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckIcon, CopyIcon } from "lucide-react";
import { api } from "../../api/client";
import { Button } from "../ui/button";
import { Spinner } from "../ui/spinner";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!text || !navigator.clipboard) return;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <Button type="button" variant="outline" size="xs" onClick={() => void copy()} aria-label={label}>
      {copied ? <CheckIcon className="text-success" /> : <CopyIcon />}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function CommandBlock({ text }: { text: string }) {
  return (
    <pre className="overflow-x-auto rounded-md border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-foreground">
      {text}
    </pre>
  );
}

export function AgentSettings() {
  const { data, isPending, error } = useQuery({
    queryKey: ["settings-agent"],
    queryFn: api.settings.agent,
  });
  const test = useMutation({ mutationFn: api.settings.testAgent });
  const installed = data?.installed === true;

  return (
    <>
      <SettingsSection id="agent-cli" title="Connect an agent">
        <SettingsRow
          title="anteroom command"
          description="An agent on this Mac uses this command to do what the app already allows. It does not publish or delete unless you confirm it."
          status={
            isPending
              ? "Looking up the command…"
              : installed
                ? `${data.on_path ? "Installed and on your PATH" : "Installed"}${data.version ? `, version ${data.version}` : ""}.`
                : "Not installed."
          }
          control={
            <Button
              type="button"
              variant="outline"
              size="xs"
              disabled={test.isPending}
              onClick={() => test.mutate()}
            >
              {test.isPending ? <Spinner /> : null}
              Test connection
            </Button>
          }
        >
          {data?.path ? (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <CommandBlock text={data.path} />
              </div>
              <CopyButton text={data.path} label="Copy command path" />
            </div>
          ) : null}
          {data?.install_command ? (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <CommandBlock text={data.install_command} />
              </div>
              <CopyButton text={data.install_command} label="Copy install command" />
            </div>
          ) : null}
          {data?.hint ? <SettingsNote>{data.hint}</SettingsNote> : null}
          {data?.version_error ? <SettingsNote tone="warning">{data.version_error}</SettingsNote> : null}
          {error ? <SettingsNote tone="error">{error.message}</SettingsNote> : null}
          {test.data ? (
            <SettingsNote tone={test.data.ok ? "success" : "error"}>{test.data.detail}</SettingsNote>
          ) : null}
          {test.error ? <SettingsNote tone="error">{test.error.message}</SettingsNote> : null}
        </SettingsRow>
        <SettingsRow
          id="agent-address"
          title="Address"
          description="An agent on this Mac uses the local address. Another machine on your tailnet uses the Tailscale address when Tailscale HTTPS is on in Connections."
        >
          {data?.base_url ? (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <CommandBlock text={data.base_url} />
              </div>
              <CopyButton text={data.base_url} label="Copy local address" />
            </div>
          ) : null}
          {data?.login_command ? (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <CommandBlock text={data.login_command} />
              </div>
              <CopyButton text={data.login_command} label="Copy login command" />
            </div>
          ) : null}
          {data?.remote_base_url ? (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <CommandBlock text={data.remote_base_url} />
              </div>
              <CopyButton text={data.remote_base_url} label="Copy Tailscale address" />
            </div>
          ) : null}
          {data?.remote_login_command ? (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <CommandBlock text={data.remote_login_command} />
              </div>
              <CopyButton text={data.remote_login_command} label="Copy Tailscale login command" />
            </div>
          ) : null}
        </SettingsRow>
      </SettingsSection>
      <SettingsSection id="agent-clients" title="Agent apps">
        {(data?.clients ?? []).map((client) => (
          <SettingsRow key={client.id} id={`agent-${client.id}`} title={client.label} description={client.where}>
            {client.command ? (
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <CommandBlock text={client.command} />
                </div>
                <CopyButton text={client.command} label={`Copy ${client.label} command`} />
              </div>
            ) : null}
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <CommandBlock text={client.snippet} />
              </div>
              <CopyButton text={client.snippet} label={`Copy ${client.label} config`} />
            </div>
          </SettingsRow>
        ))}
        {!isPending && data && data.clients.length === 0 ? (
          <SettingsRow title="No setup yet" description="The app did not return a client snippet." />
        ) : null}
      </SettingsSection>
    </>
  );
}
