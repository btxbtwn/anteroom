/** Browser-local transport to the Chrome extension. Supabase sessions never cross it. */
import type { MarketplaceForm } from "./types";
import type { SignedCommand } from "./nativeMarketplaces";
import { HOSTED_ORIGIN as ORIGIN } from "../lib/macApp";
export interface CloudConnection {
  ok: boolean;
  state: "connected" | "disconnected" | "pending" | "unavailable";
  extensionVersion: string;
  supportedActions?: string[];
}
export interface BrowserAccount { id: string; email: string }
/** What Chrome said about a Save draft. The listing's record on the server has the details. */
export interface ChromeDraftResult { ok: boolean; recorded: boolean; error_code?: string }
const SUPPORTED = ["poshmark.form.read", "marketplace.save_draft"];
export const SAVE_DRAFT = "marketplace.save_draft";
const ERRORS: Record<string, string> = {
  not_connected: "Connect Chrome before loading the Poshmark form.",
  account_changed: "The browser connection changed. Connect Chrome and load the form again.",
  busy: "Chrome is busy with another listing action. Try again when it finishes.",
  signed_out: "Sign in to Poshmark in this Chrome profile, then load the form again.",
  challenge: "Open Poshmark in Chrome and finish its browser check, then try again.",
  rate_limited: "Poshmark asked you to wait. Try again later.",
  schema_drift: "Poshmark's form changed. Anteroom needs an update before showing it.",
};
// Only failures where the listing's record may not say what happened yet.
const DRAFT_ERRORS: Record<string, string> = {
  not_connected: "Connect Chrome to save drafts, then try again.",
  account_changed: "The Chrome connection changed. Connect Chrome and save the draft again.",
  invalid_command: "Chrome could not accept this Save draft. Update Anteroom Bridge, reload this page and try again.",
  busy: "Chrome is still saving this draft.",
};
type Action = "status" | "connect" | "disconnect" | "poshmark_form" | "marketplace_save_draft";
export function cloudExtension(account: BrowserAccount, channel: Window = window) {
  function send(action: Action, extra?: { listing?: Record<string, string | number | null>; command?: SignedCommand }): Promise<Record<string, unknown>> {
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
        if (action === "marketplace_save_draft") {
          finish({ ok: data.ok === true, recorded: data.recorded === true, ...(data.ok === true ? {} : { error_code: String(data.error_code || "unavailable") }) }); return;
        }
        finish({ ok: data.ok === true, state: ["connected", "disconnected", "pending"].includes(data.state) ? data.state : "unavailable",
          extensionVersion: typeof data.extensionVersion === "string" ? data.extensionVersion.slice(0, 30) : "",
          supportedActions: Array.isArray(data.supportedActions) ? data.supportedActions.filter((item: unknown) => SUPPORTED.includes(item as string)) : [] });
      };
      // A save uploads photos and reads the draft back; Chrome gives each step three minutes.
      const wait = action === "marketplace_save_draft" ? 240000 : action === "poshmark_form" ? 35000 : 2000;
      const timer = channel.setTimeout(() => finish(action === "marketplace_save_draft" ? { ok: false, recorded: false, error_code: "timeout" }
        : { ok: false, state: "unavailable", extensionVersion: "" }), wait);
      channel.addEventListener("message", listener);
      channel.postMessage({ source: "anteroom-cloud", version: 1, requestId, action, accountId: account.id,
        ...(action === "connect" ? { email: account.email } : {}), ...extra }, ORIGIN);
    });
  }
  const status = async () => await send("status") as unknown as CloudConnection;
  return {
    status, connect: async () => await send("connect") as unknown as CloudConnection,
    disconnect: async () => await send("disconnect") as unknown as CloudConnection,
    readPoshmarkForm: async (listing: Record<string, string | number | null>): Promise<MarketplaceForm> => {
      const connection = await status();
      if (!connection.ok || connection.state !== "connected") throw new Error(ERRORS.not_connected);
      if (!connection.supportedActions?.includes("poshmark.form.read")) throw new Error("Use Anteroom Bridge 0.2.39 or later. If you also use the local app, update Anteroom and use its Connect Chrome, then reload this page.");
      const result = await send("poshmark_form", { listing });
      const form = result.form as MarketplaceForm | undefined;
      if (!result.ok || form?.marketplace !== "poshmark" || form.native !== true || !Array.isArray(form.fields)) {
        throw new Error(ERRORS[String(result.error_code)] || "Could not load the Poshmark form. Please try again.");
      }
      return form;
    },
    /**
     * Hand the server's signed Save draft to Chrome and wait for it. Chrome reports the
     * result to Anteroom itself; this throws only when the listing may not show it yet.
     */
    saveDraft: async (command: SignedCommand): Promise<ChromeDraftResult> => {
      const result = await send("marketplace_save_draft", { command: { body: command.body, signature: command.signature } }) as unknown as ChromeDraftResult;
      if (result.recorded) return result;
      if (result.ok) throw new Error("Chrome saved the draft but could not tell Anteroom. Anteroom will ask you to check it.");
      if (result.error_code === "timeout") throw new Error("Chrome is still working on the draft. Anteroom shows the result when Chrome reports it.");
      throw new Error(DRAFT_ERRORS[String(result.error_code)] || "Chrome could not report what happened. Check the marketplace's drafts before trying again.");
    },
  };
}
export type CloudBridge = ReturnType<typeof cloudExtension>;
