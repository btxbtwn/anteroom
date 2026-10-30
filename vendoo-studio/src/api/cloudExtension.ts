/** Browser-local connection only. Supabase sessions never cross this transport. */
export interface CloudConnection {
  ok: boolean;
  state: "connected" | "disconnected" | "pending" | "unavailable";
  extensionVersion: string;
}
export interface BrowserAccount { id: string; email: string }
const ORIGIN = "https://peaceful-meadowstone-6944.fly.dev";
export function cloudExtension(account: BrowserAccount, channel: Window = window) {
  function send(action: "status" | "connect" | "disconnect"): Promise<CloudConnection> {
    if (channel.location.origin !== ORIGIN) return Promise.resolve({ ok: false, state: "unavailable", extensionVersion: "" });
    const requestId = crypto.randomUUID();
    return new Promise(resolve => {
      const finish = (response: CloudConnection) => {
        channel.clearTimeout(timer); channel.removeEventListener("message", listener); resolve(response);
      };
      const listener = (event: MessageEvent) => {
        const data = event.data;
        if (event.source !== channel || event.origin !== ORIGIN || data?.source !== "anteroom-extension" || data.version !== 1 || data.requestId !== requestId) return;
        finish({ ok: data.ok === true, state: ["connected", "disconnected", "pending"].includes(data.state) ? data.state : "unavailable",
          extensionVersion: typeof data.extensionVersion === "string" ? data.extensionVersion.slice(0, 30) : "" });
      };
      const timer = channel.setTimeout(() => finish({ ok: false, state: "unavailable", extensionVersion: "" }), 2000);
      channel.addEventListener("message", listener);
      channel.postMessage({ source: "anteroom-cloud", version: 1, requestId, action, accountId: account.id,
        ...(action === "connect" ? { email: account.email } : {}) }, ORIGIN);
    });
  }
  return { status: () => send("status"), connect: () => send("connect"), disconnect: () => send("disconnect") };
}
