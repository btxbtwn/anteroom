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
  "marketplace.execute": 200_000, "marketplace.result": 75_000, "marketplace.delete": 100_000, "job.cancel": 10_000,
  "marketplace.vinted_categories": 45_000, "marketplace.vinted_brands": 45_000,
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
        finish({ ok: data.ok === true, state: ["connected", "disconnected", "pending"].includes(data.state) ? data.state : "unavailable",
          extensionVersion: typeof data.extensionVersion === "string" ? data.extensionVersion.slice(0, 30) : "",
          supportedActions: Array.isArray(data.supportedActions) ? data.supportedActions.filter((item: unknown) => item === "poshmark.form.read" || item === "marketplace.actions" || item === "vinted.choices") : [] });
      };
      const timeout = relay ? COMMAND_TIMEOUT_MS[relay.command.type] ?? 10_000 : action === "poshmark_form" ? 35000 : 2000;
      const timer = channel.setTimeout(() => finish({ ok: false, state: "unavailable", extensionVersion: "" }), timeout);
      channel.addEventListener("message", listener);
      channel.postMessage({ source: "anteroom-cloud", version: 1, requestId, action, accountId: account.id,
        ...(action === "connect" ? { email: account.email } : {}), ...(listing ? { listing } : {}), ...relay }, ORIGIN);
    });
  }
  const status = async () => await send("status") as unknown as CloudConnection;
  return {
    status, connect: async () => await send("connect") as unknown as CloudConnection,
    disconnect: async () => await send("disconnect") as unknown as CloudConnection,
    /** Run one relayed command in Chrome. Returns the Bridge's reply, or a failure the server records as unconfirmed. */
    runMarketplaceCommand: async (command: RelayedCommand, photos: RelayedPhoto[]): Promise<Record<string, unknown>> => {
      const result = await send("marketplace_command", undefined, { command, photos });
      const reply = result.reply;
      if (result.ok && reply && typeof reply === "object" && !Array.isArray(reply)) return reply as Record<string, unknown>;
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
