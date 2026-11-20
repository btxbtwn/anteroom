export interface AgentClientSnippet {
  id: string;
  label: string;
  where: string;
  snippet: string;
  command: string | null;
}

export interface AgentConnectionStatus {
  installed: boolean;
  on_path: boolean;
  path: string | null;
  version: string | null;
  version_error: string | null;
  base_url: string;
  remote_base_url: string | null;
  login_command: string;
  remote_login_command: string | null;
  install_command: string | null;
  hint: string | null;
  clients: AgentClientSnippet[];
}

export interface AgentConnectionTest {
  ok: boolean;
  detail: string;
  version: string | null;
  base_url: string | null;
}
