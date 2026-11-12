import type { OpenClaudeTerminal } from "../components/ClaudeLoginTerminal";

type Subscription = "chatgpt" | "claude";
interface SubscriptionStatus {
  signed_in: boolean;
  installed?: boolean;
  listing_model: string;
  pending?: { authorize_url: string } | null;
  error?: string | null;
}
export interface ConnectorStatus {
  connection: string;
  paused: boolean;
  active_calls: number;
  chatgpt: SubscriptionStatus;
  claude: SubscriptionStatus;
}
export interface ConnectorBridge {
  connector_status: () => Promise<ConnectorStatus>;
  connector_pause: (paused: boolean) => Promise<unknown>;
  connector_chatgpt_login: () => Promise<unknown>;
  connector_chatgpt_logout: () => Promise<unknown>;
  connector_setup_claude: () => Promise<unknown>;
  connector_models: (provider: Subscription) => Promise<{ value: string; label: string }[]>;
  connector_set_model: (provider: Subscription, model: string) => Promise<unknown>;
  connector_claude_start: (cols: number, rows: number) => Promise<{ id: string }>;
  connector_claude_read: (id: string) => Promise<{ data?: number[]; code?: number; waiting?: boolean }>;
  connector_claude_send: (id: string, message: object) => Promise<unknown>;
  connector_claude_close: (id: string) => Promise<unknown>;
}

export function connectorBridge(): ConnectorBridge | null {
  const api = (window as unknown as { pywebview?: { api?: ConnectorBridge } }).pywebview?.api;
  return api?.connector_status ? api : null;
}

export function nativeClaudeTerminal(bridge: ConnectorBridge): OpenClaudeTerminal {
  return (cols, rows, handlers) => {
    let active = true;
    let identity: string | null = null;
    void bridge.connector_claude_start(cols, rows).then(async ({ id }) => {
      identity = id;
      if (!active) { await bridge.connector_claude_close(id); return; }
      while (active) {
        const frame = await bridge.connector_claude_read(id);
        if (!active) return;
        if (frame.data) handlers.message(new Uint8Array(frame.data));
        else if (!frame.waiting) {
          handlers.message(JSON.stringify({ type: "exit", code: frame.code }));
          return;
        }
      }
    }).catch(() => {
      if (active) handlers.message(JSON.stringify({ type: "error", message: "Claude sign-in did not finish. Close it and try again." }));
    });
    return {
      send: message => {
        if (identity && active) void bridge.connector_claude_send(identity, message).catch(() => handlers.close());
      },
      close: () => {
        active = false;
        if (identity) void bridge.connector_claude_close(identity).catch(() => {});
      },
    };
  };
}
