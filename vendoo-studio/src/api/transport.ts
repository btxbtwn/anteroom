/** The existing API client uses this for desktop HTTP and authenticated cloud HTTP. */
export interface HostedApiSession {
  origin: string;
  accessToken: () => Promise<string | null>;
  unauthorized: () => void;
}
let hostedSession: HostedApiSession | null = null;

export function installHostedApiSession(session: HostedApiSession): () => void {
  hostedSession = session;
  return () => { if (hostedSession === session) hostedSession = null; };
}

export function hasHostedApiSession() { return hostedSession !== null; }

/** The signed-in session, so per-account caches can tell when it changes. */
export function currentHostedSession(): HostedApiSession | null { return hostedSession; }

export async function studioFetch(input: string, options: RequestInit = {}): Promise<Response> {
  const session = hostedSession;
  if (!session) return fetch(input, options);
  const url = new URL(input, session.origin);
  if (url.origin !== session.origin || !url.pathname.startsWith("/api/")) {
    throw new Error("Anteroom API requests must stay on this app's origin.");
  }
  const token = await session.accessToken();
  if (!token || hostedSession !== session) throw new Error("Sign in to continue.");
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url.href, { ...options, headers, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer" });
  if (hostedSession !== session) throw new Error("Your account changed. Open the request again.");
  if (response.status === 401 && hostedSession === session) session.unauthorized();
  return response;
}

/** Read an authenticated export without putting a session in its URL. */
export async function studioBlob(input: string): Promise<Blob> {
  const session = hostedSession;
  const response = await studioFetch(input);
  if (!response.ok) throw new Error("Could not download this export. Please try again.");
  const blob = await response.blob();
  if (hostedSession !== session) throw new Error("Your account changed. Download the export again.");
  return blob;
}

/**
 * A request the server refused or couldn't answer. `retryable` marks a read
 * (GET) that failed only because the server was briefly unavailable, so asking
 * again is safe; anything else may already have run and is never repeated.
 */
export class ApiRequestError extends Error {
  readonly status: number;
  readonly retryable: boolean;
  readonly retryAfterMs: number | null;
  /** Why the server refused, when it says (`X-Anteroom-Reason`), e.g. "marketplace_busy". */
  readonly reason: string | null;
  constructor(message: string, status: number, retryable: boolean, retryAfterMs: number | null = null, reason: string | null = null) {
    super(message);
    this.status = status;
    this.retryable = retryable;
    this.retryAfterMs = retryAfterMs;
    this.reason = reason;
  }
}

/** `Retry-After` in milliseconds: a number of seconds or an HTTP date. */
export function retryAfterMs(value: string | null, now = Date.now()): number | null {
  if (!value?.trim()) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const date = Date.parse(value);
  return Number.isNaN(date) ? null : Math.max(0, date - now);
}
