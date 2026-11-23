export interface AgentClientSnippet {
  id: string;
  label: string;
  where: string;
  snippet: string;
  prompt: string;
  command: string | null;
}

export interface AgentConnectionStatus {
  hosted?: boolean;
  installed: boolean;
  on_path: boolean;
  path: string | null;
  version: string | null;
  version_error: string | null;
  base_url: string;
  remote_base_url: string | null;
  mcp_url?: string | null;
  login_command: string;
  remote_login_command: string | null;
  install_command: string | null;
  hint: string | null;
  clients: AgentClientSnippet[];
  remote_clients?: AgentClientSnippet[];
}

export interface AgentConnectionTest {
  ok: boolean;
  detail: string;
  version: string | null;
  base_url: string | null;
}

export type McpPermissionArea = "listings" | "analytics" | "attention";
export type McpPermissionLevel = "none" | "read" | "write";
export type McpTokenPermissions = Record<McpPermissionArea, McpPermissionLevel>;

export interface McpAccessToken {
  id: string;
  name: string;
  token_prefix: string;
  created_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  permissions: McpTokenPermissions;
  active: boolean;
}

export interface McpTokenCreateResponse extends McpAccessToken {
  token: string;
}
