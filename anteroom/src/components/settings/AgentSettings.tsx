import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckIcon, CopyIcon, KeyRoundIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { api } from "../../api/client";
import type {
  AgentClientSnippet,
  McpAccessToken,
  McpPermissionArea,
  McpPermissionLevel,
  McpTokenCreateResponse,
  McpTokenPermissions,
} from "../../api/agentConnection";
import { useWorkspaceRuntime } from "../../app/runtime";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "../ui/select";
import { Spinner } from "../ui/spinner";
import { toastManager } from "../ui/toast";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

const EXPIRY_OPTIONS = [
  { value: "none", label: "Never expires" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
] as const;

const LEVEL_LABELS: Record<McpPermissionLevel, string> = {
  none: "No access",
  read: "Read",
  write: "Read and write",
};

/* What each area covers and which levels it offers; matches services/mcp_tokens.py. */
const PERMISSION_AREAS: ReadonlyArray<{
  area: McpPermissionArea;
  label: string;
  levels: readonly McpPermissionLevel[];
}> = [
  { area: "listings", label: "Listings", levels: ["none", "read", "write"] },
  { area: "analytics", label: "Analytics", levels: ["none", "read"] },
  { area: "attention", label: "Needs Attention", levels: ["none", "read", "write"] },
];

const NEW_TOKEN_PERMISSIONS: McpTokenPermissions = { listings: "read", analytics: "read", attention: "read" };

function PermissionSelects({
  permissions,
  onChange,
  disabled,
  labelPrefix,
}: {
  permissions: McpTokenPermissions;
  onChange: (area: McpPermissionArea, level: McpPermissionLevel) => void;
  disabled?: boolean;
  labelPrefix: string;
}) {
  return (
    <div className="grid gap-2 sm:flex sm:flex-wrap sm:items-center sm:gap-x-3">
      {PERMISSION_AREAS.map(({ area, label, levels }) => (
        <label
          key={area}
          className="grid grid-cols-[7.5rem_minmax(0,1fr)] items-center gap-1.5 text-xs text-muted-foreground sm:flex"
        >
          <span>{label}</span>
          <Select
            value={permissions[area]}
            disabled={disabled}
            onValueChange={(value) => {
              if (value && value !== permissions[area]) onChange(area, value as McpPermissionLevel);
            }}
          >
            <SelectTrigger size="sm" className="w-full sm:w-36" aria-label={`${labelPrefix} ${label} access`}>
              <SelectValue>{LEVEL_LABELS[permissions[area]]}</SelectValue>
            </SelectTrigger>
            <SelectPopup>
              {levels.map((level) => (
                <SelectItem key={level} value={level}>
                  {LEVEL_LABELS[level]}
                </SelectItem>
              ))}
            </SelectPopup>
          </Select>
        </label>
      ))}
    </div>
  );
}

function CopyButton({ text, label, buttonText = "Copy" }: { text: string; label: string; buttonText?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      setCopied(false);
      toastManager.add({ type: "error", title: "Could not copy", description: "Check clipboard permissions and try again." });
      return;
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <>
      <Button type="button" variant="outline" size="xs" onClick={() => void copy()} aria-label={label}>
        {copied ? <CheckIcon className="text-success" /> : <CopyIcon />}
        {copied ? "Copied" : buttonText}
      </Button>
      <span role="status" className="sr-only">{copied ? "Copied to clipboard." : ""}</span>
    </>
  );
}

function CommandBlock({ text }: { text: string }) {
  return (
    <pre className="overflow-x-auto rounded-md border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-foreground">
      {text}
    </pre>
  );
}

function AgentClientRow({ id, client }: { id: string; client: AgentClientSnippet }) {
  return (
    <SettingsRow
      id={id}
      title={client.label}
      description={client.where}
      control={<CopyButton text={client.prompt} label={`Copy setup prompt for ${client.label}`} buttonText="Copy setup prompt" />}
    >
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
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "Never";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function AgentSettings() {
  const runtime = useWorkspaceRuntime();
  const { data, isPending, error } = useQuery({
    queryKey: ["settings-agent"],
    queryFn: api.settings.agent,
  });
  const tokensQuery = useQuery({
    queryKey: ["settings-mcp-tokens"],
    queryFn: api.settings.mcpTokens.list,
  });
  const test = useMutation({ mutationFn: api.settings.testAgent });

  const [tokenName, setTokenName] = useState("");
  const [expiryChoice, setExpiryChoice] = useState<string>("90");
  const [newPermissions, setNewPermissions] = useState<McpTokenPermissions>(NEW_TOKEN_PERMISSIONS);
  const [createdToken, setCreatedToken] = useState<McpTokenCreateResponse | null>(null);

  const createToken = useMutation({
    mutationFn: api.settings.mcpTokens.create,
    onSuccess: (created) => {
      setCreatedToken(created);
      setTokenName("");
      setNewPermissions(NEW_TOKEN_PERMISSIONS);
      void tokensQuery.refetch?.();
    },
  });

  const revokeToken = useMutation({
    mutationFn: api.settings.mcpTokens.revoke,
    onSuccess: () => {
      void tokensQuery.refetch?.();
    },
  });

  const updatePermissions = useMutation({
    mutationFn: api.settings.mcpTokens.updatePermissions,
    onSuccess: () => {
      void tokensQuery.refetch?.();
    },
  });

  const isHosted = runtime.hosted || data?.hosted === true;
  const installed = data?.installed === true;
  const tokens: McpAccessToken[] = tokensQuery.data?.tokens ?? [];
  const activeTokenCount = tokens.filter((t) => t.active).length;
  const remoteClients = data?.remote_clients ?? [];

  const handleCreateToken = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = tokenName.trim();
    if (!trimmed) return;
    const expires_in_days = expiryChoice === "none" ? null : Number(expiryChoice);
    createToken.mutate({ name: trimmed, expires_in_days, permissions: newPermissions });
  };

  return (
    <>
      <SettingsSection id="agent-remote-mcp" title="Remote MCP server">
        <SettingsRow
          id="agent-mcp-url"
          title="Streamable HTTP endpoint"
          description="Agents can read inventory, analytics, offers, sourcing ROI, and Needs Attention, stage local drafts or edits, and dismiss alerts, as far as each token allows. They can never publish, update live marketplaces, or delist."
        >
          {data?.mcp_url ? (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <CommandBlock text={data.mcp_url} />
              </div>
              <CopyButton text={data.mcp_url} label="Copy MCP endpoint URL" />
            </div>
          ) : isPending ? (
            <SettingsNote>Loading MCP endpoint…</SettingsNote>
          ) : null}
          {error ? <SettingsNote tone="error">{error.message}</SettingsNote> : null}
        </SettingsRow>

        <SettingsRow
          id="agent-mcp-tokens"
          title="Access tokens"
          description="Personal tokens authenticate MCP clients to your account only. Choose what each token can read or change; changes apply to its next request. Tokens cannot publish or delist listings and cannot access other API routes."
          status={
            tokensQuery.isPending
              ? "Loading tokens…"
              : activeTokenCount === 0
                ? "No active tokens."
                : `${activeTokenCount} active ${activeTokenCount === 1 ? "token" : "tokens"}.`
          }
        >
          <form onSubmit={handleCreateToken} className="flex flex-wrap items-center gap-2">
            <Input
              type="text"
              value={tokenName}
              onChange={(event) => setTokenName(event.target.value)}
              placeholder="Token label (e.g. Claude on iPad)"
              aria-label="New MCP access token label"
              className="min-w-48 flex-1"
              maxLength={120}
            />
            <Select value={expiryChoice} onValueChange={(value) => setExpiryChoice(value ?? "90")}>
              <SelectTrigger size="sm" className="w-36" aria-label="Token expiration">
                <SelectValue>
                  {EXPIRY_OPTIONS.find((opt) => opt.value === expiryChoice)?.label ?? "90 days"}
                </SelectValue>
              </SelectTrigger>
              <SelectPopup>
                {EXPIRY_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectPopup>
            </Select>
            <Button type="submit" size="sm" disabled={createToken.isPending || !tokenName.trim()}>
              {createToken.isPending ? <Spinner /> : <PlusIcon />}
              Create token
            </Button>
            <div className="basis-full">
              <PermissionSelects
                permissions={newPermissions}
                labelPrefix="New token"
                onChange={(area, level) => setNewPermissions((current) => ({ ...current, [area]: level }))}
              />
            </div>
          </form>

          {createToken.error ? <SettingsNote tone="error">{createToken.error.message}</SettingsNote> : null}
          {revokeToken.error ? <SettingsNote tone="error">{revokeToken.error.message}</SettingsNote> : null}
          {updatePermissions.error ? (
            <SettingsNote tone="error">{updatePermissions.error.message}</SettingsNote>
          ) : null}
          {tokensQuery.error ? <SettingsNote tone="error">{tokensQuery.error.message}</SettingsNote> : null}

          {createdToken ? (
            <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/40 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                  <KeyRoundIcon className="size-3.5 text-muted-foreground" />
                  Copy your new token now — it will not be shown again.
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => setCreatedToken(null)}
                >
                  Hide
                </Button>
              </div>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <CommandBlock text={createdToken.token} />
                </div>
                <CopyButton text={createdToken.token} label="Copy new access token" />
              </div>
            </div>
          ) : null}

          {tokens.length > 0 ? (
            <div className="divide-y divide-border/60 rounded-lg border border-border/60 bg-card/30">
              {tokens.map((tok) => (
                <div key={tok.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 text-xs">
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium text-foreground">{tok.name}</span>
                      <Badge variant={tok.active ? "secondary" : "outline"} size="sm">
                        {tok.active ? "Active" : "Revoked"}
                      </Badge>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-muted-foreground tabular-nums">
                      <span className="font-mono">{tok.token_prefix}…</span>
                      <span>Created {formatDate(tok.created_at)}</span>
                      <span>Last used {formatDate(tok.last_used_at)}</span>
                      {tok.expires_at ? <span>Expires {formatDate(tok.expires_at)}</span> : null}
                    </div>
                  </div>
                  {tok.active ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      disabled={revokeToken.isPending}
                      onClick={() => revokeToken.mutate(tok.id)}
                      aria-label={`Revoke ${tok.name}`}
                    >
                      <Trash2Icon />
                      Revoke
                    </Button>
                  ) : null}
                  {tok.active ? (
                    <div className="basis-full">
                      <PermissionSelects
                        permissions={tok.permissions}
                        labelPrefix={tok.name}
                        disabled={updatePermissions.isPending}
                        onChange={(area, level) =>
                          updatePermissions.mutate({ tokenId: tok.id, permissions: { [area]: level } })
                        }
                      />
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </SettingsRow>
      </SettingsSection>

      {remoteClients.length > 0 ? (
        <SettingsSection id="agent-remote-clients" title="HTTP client setup">
          {remoteClients.map((client) => (
            <AgentClientRow key={client.id} id={`agent-remote-${client.id}`} client={client} />
          ))}
        </SettingsSection>
      ) : null}

      {!isHosted ? (
        <>
          <SettingsSection id="agent-cli" title="Connect an agent">
            <SettingsRow
              title="anteroom command"
              description="Lets an agent on this Mac do what the app allows; it never publishes or deletes without your confirmation."
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
              {test.data ? (
                <SettingsNote tone={test.data.ok ? "success" : "error"}>{test.data.detail}</SettingsNote>
              ) : null}
              {test.error ? <SettingsNote tone="error">{test.error.message}</SettingsNote> : null}
            </SettingsRow>
            <SettingsRow
              id="agent-address"
              title="Address"
              description="Use the local address on this Mac, or the Tailscale address from another machine once Tailscale HTTPS is on."
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
              <AgentClientRow key={client.id} id={`agent-${client.id}`} client={client} />
            ))}
            {!isPending && data && data.clients.length === 0 ? (
              <SettingsRow title="No setup yet" description="The app did not return a client snippet." />
            ) : null}
          </SettingsSection>
        </>
      ) : null}
    </>
  );
}
