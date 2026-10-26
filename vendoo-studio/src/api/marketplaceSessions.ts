/** Chrome sign-in for marketplaces that have no official seller API. */

export const EXTENSION_SESSION_MARKETS = ["depop", "poshmark", "mercari", "sellwild", "vinted"] as const;

export type ExtensionSessionMarket = (typeof EXTENSION_SESSION_MARKETS)[number];

export type ExtensionSessionState = "signed_in" | "expired" | "signed_out" | "unknown";

export interface ExtensionSession {
  state: ExtensionSessionState;
  /** Unix seconds, when Chrome or the token says so. */
  expires_at: number | null;
  label: string | null;
  expiring: boolean;
}

export interface ExtensionSessions {
  sessions: Record<ExtensionSessionMarket, ExtensionSession>;
}

export interface ExtensionSessionConnect {
  ok: boolean;
  signed_in?: boolean;
  state?: ExtensionSessionState;
  expires_at?: number | null;
  label?: string | null;
  expiring?: boolean;
  error?: string;
}

const EXPIRING_MS = 24 * 60 * 60 * 1000;

export const EXTENSION_SESSION_NAMES: Record<ExtensionSessionMarket, string> = {
  depop: "Depop",
  poshmark: "Poshmark",
  mercari: "Mercari",
  sellwild: "Sellwild",
  vinted: "Vinted",
};

export function sessionIsExpiring(session: ExtensionSession, now = Date.now()): boolean {
  return session.state === "signed_in"
    && session.expires_at != null
    && session.expires_at * 1000 > now
    && session.expires_at * 1000 - now <= EXPIRING_MS;
}

export function chromeSessionPresentation(
  session: ExtensionSession | undefined,
  chromeReady: boolean,
  now = Date.now(),
): { tone: "success" | "warning" | "error" | "muted"; label: string } {
  if (!chromeReady) return { tone: "muted", label: "Connect Chrome first" };
  if (!session || session.state === "unknown") return { tone: "muted", label: "Could not check" };
  if (session.state === "expired") return { tone: "error", label: "Sign in again" };
  if (session.state === "signed_out") return { tone: "muted", label: "Not signed in" };
  const expiry = expiryPhrase(session.expires_at, now);
  const who = session.label ? `Signed in as ${session.label}` : "Signed in";
  if (sessionIsExpiring(session, now)) return { tone: "warning", label: expiry ? `${who} · ${expiry}` : `${who} · expires soon` };
  return { tone: "success", label: expiry ? `${who} · ${expiry}` : who };
}

function expiryPhrase(expiresAt: number | null, now: number): string | null {
  if (expiresAt == null) return null;
  const when = expiresAt * 1000;
  if (when <= now) return null;
  const date = new Date(when);
  if (date.toDateString() === new Date(now).toDateString()) return "expires today";
  return `expires ${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

export interface SessionAlert {
  key: string;
  title: string;
  description: string;
}

/** One notice when a sign-in has actually ended. A same-day expiry is only a status line. */
export function sessionNotification(
  id: string,
  name: string,
  session: ExtensionSession | undefined,
  previous: ExtensionSessionState | undefined,
  hadSignIn = false,
): SessionAlert | null {
  if (!session || session.state === "unknown") return null;
  if (session.state === "expired") {
    return {
      key: `${id}:expired:${session.expires_at ?? "cookie"}`,
      title: `${name} sign-in expired`,
      description: "Sign in again so listing can continue.",
    };
  }
  if (session.state === "signed_out" && (previous === "signed_in" || hadSignIn)) {
    return {
      key: `${id}:signed_out`,
      title: `${name} needs a sign-in`,
      description: "Chrome no longer has a saved sign-in. Sign in again so listing can continue.",
    };
  }
  return null;
}
