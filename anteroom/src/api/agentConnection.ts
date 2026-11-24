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

export type McpPermissionArea = "listings" | "analytics" | "attention" | "marketplaces";
export type McpPermissionLevel = "none" | "read" | "write" | "draft" | "publish";
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

/** An app connected by signing in (Claude, ChatGPT, Gemini); hosted only. */
export interface McpConnection {
  client_id: string;
  client_name: string | null;
  permissions: McpTokenPermissions;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  /** When Supabase confirmed the disconnected app's sign-in ended; null until then. */
  signed_out_at: string | null;
}

export interface McpConnectionRevoked extends McpConnection {
  /** False when Supabase could not confirm the app was signed out; it stays off in Anteroom. */
  signed_out: boolean;
}
