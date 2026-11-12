/** Browser-local transport to Anteroom Bridge. Supabase sessions never cross it. */
import type { MarketplaceForm } from "./types";
import { HOSTED_ORIGIN as ORIGIN } from "../lib/macApp";
export interface CloudConnection {
  ok: boolean;
  state: "connected" | "disconnected" | "pending" | "unavailable";
  extensionVersion: string;
  supportedActions?: string[];
}
export interface BrowserAccount { id: string; email: string }
/** A marketplace command the hosted API relays to this seller's Chrome. */
export interface RelayedCommand { type: string; job_id: string | null; message_id: string; payload: Record<string, unknown> }
export interface RelayedPhoto { id: string; mime_type: string; base64: string }
// How long each command may take in Chrome, a little past the server's own wait.
const COMMAND_TIMEOUT_MS: Record<string, number> = {
  "job.vendoo_api": 250_000,
  "marketplace.promotion_check": 100_000, "marketplace.promote": 160_000, "marketplace.promotion_result": 30_000, "marketplace.like_events": 55_000,
  "marketplace.sales_history": 1_210_000,
  "marketplace.execute": 290_000, "marketplace.result": 75_000, "marketplace.delete": 100_000, "job.cancel": 10_000,
  "marketplace.sessions": 30_000, "marketplace.connect": 50_000,
  "marketplace.import.page": 130_000, "marketplace.import.listing": 130_000,
};
const ERRORS: Record<string, string> = {
  not_connected: "Connect Chrome before loading the Poshmark form.",
  account_changed: "The browser connection changed. Connect Chrome and load the form again.",
  busy: "Chrome is busy with another listing action. Try again when it finishes.",
  signed_out: "Sign in to Poshmark in this Chrome profile, then load the form again.",
  challenge: "Open Poshmark in Chrome and finish its browser check, then try again.",
  rate_limited: "Poshmark asked you to wait. Try again later.",
  schema_drift: "Poshmark's form changed. Anteroom needs an update before showing it.",
};
const BRIDGE_ACTIONS = ["poshmark.form.read", "marketplace.actions", "marketplace.sessions", "marketplace.sales_history", "marketplace.promotions", "marketplace.import", "vendoo.drafts", "marketplace.signed_approvals", "marketplace.signed_commands"];
/** The connection a Bridge reported, keeping only states and actions this page knows. */
function toConnection(data: Record<string, unknown>): CloudConnection {
  const state = data.state === "connected" || data.state === "disconnected" || data.state === "pending" ? data.state : "unavailable";
  return { ok: data.ok === true, state,
    extensionVersion: typeof data.extensionVersion === "string" ? data.extensionVersion.slice(0, 30) : "",
    supportedActions: Array.isArray(data.supportedActions) ? data.supportedActions.filter((item): item is string => typeof item === "string" && BRIDGE_ACTIONS.includes(item)) : [] };
}
export function cloudExtension(account: BrowserAccount, channel: Window = window) {
  function send(action: "status" | "connect" | "disconnect" | "poshmark_form" | "marketplace_command",
    listing?: Record<string, string | number | null>, relay?: { command: RelayedCommand; photos: RelayedPhoto[] }): Promise<Record<string, unknown>> {
    if (channel.location.origin !== ORIGIN) return Promise.resolve({ ok: false, state: "unavailable", extensionVersion: "" });
    const requestId = crypto.randomUUID();
    return new Promise(resolve => {
      const finish = (response: Record<string, unknown>) => {
        channel.clearTimeout(timer); channel.removeEventListener("message", listener); resolve(response);
      };
      const listener = (event: MessageEvent) => {
        const data = event.data;
        if (event.source !== channel || event.origin !== ORIGIN || data?.source !== "anteroom-extension" || data.version !== 1 || data.requestId !== requestId) return;
        if (action === "poshmark_form") {
          finish({ ok: data.ok === true, form: data.form, error_code: data.error_code }); return;
        }
        if (action === "marketplace_command") {
          finish({ ok: data.ok === true, reply: data.reply, error_code: data.error_code }); return;
        }
        finish({ ...toConnection(data) });
      };
      const timeout = relay ? COMMAND_TIMEOUT_MS[relay.command.type] ?? 10_000 : action === "poshmark_form" ? 35000 : 2000;
      const timer = channel.setTimeout(() => finish({ ok: false, state: "unavailable", extensionVersion: "" }), timeout);
      channel.addEventListener("message", listener);
      channel.postMessage({ source: "anteroom-cloud", version: 1, requestId, action, accountId: account.id,
        ...(action === "connect" ? { email: account.email } : {}), ...(listing ? { listing } : {}), ...relay }, ORIGIN);
    });
  }
  const status = async () => toConnection(await send("status"));
  return {
    status, connect: async () => toConnection(await send("connect")),
    disconnect: async () => toConnection(await send("disconnect")),
    /** Run one relayed command in Chrome. Returns the Bridge's reply, or a failure the server records as unconfirmed. */
    runMarketplaceCommand: async (command: RelayedCommand, photos: RelayedPhoto[]): Promise<Record<string, unknown>> => {
      const result = await send("marketplace_command", undefined, { command, photos });
      const reply = result.reply;
      if (result.ok && reply && typeof reply === "object" && !Array.isArray(reply)) return reply as Record<string, unknown>;
      // The Bridge refused the command before running it: nothing reached the marketplace.
      if (["invalid_input", "not_connected", "account_changed"].includes(String(result.error_code))) {
        return { ok: false, safe_to_retry: true, error: "Chrome refused this action before starting it. Reload Anteroom and try again.", error_code: "rejected" };
      }
      return { ok: false, error: "Chrome did not finish this action.", error_code: "uncertain" };
    },
    readPoshmarkForm: async (listing: Record<string, string | number | null>): Promise<MarketplaceForm> => {
      const connection = await status();
      if (!connection.ok || connection.state !== "connected") throw new Error(ERRORS.not_connected);
      if (!connection.supportedActions?.includes("poshmark.form.read")) throw new Error("Use Anteroom Bridge 0.2.39 or later. If you also use the local app, update Anteroom and use its Connect Chrome, then reload this page.");
      const result = await send("poshmark_form", listing);
      const form = result.form as MarketplaceForm | undefined;
      if (!result.ok || form?.marketplace !== "poshmark" || form.native !== true || !Array.isArray(form.fields)) {
        throw new Error(ERRORS[String(result.error_code)] || "Could not load the Poshmark form. Please try again.");
      }
      return form;
    },
  };
}
