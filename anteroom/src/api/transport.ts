import { scheduleRequest } from "./requestScheduler";

/** The existing API client uses this for desktop HTTP and authenticated cloud HTTP. */
export interface HostedApiSession {
  origin: string;
  /** The signed-in seller, so what this device keeps for them stays theirs. */
  seller: string;
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

async function executeStudioFetch(input: string, options: RequestInit, session: HostedApiSession | null): Promise<Response> {
  if (hostedSession !== session) throw new Error("Your account changed. Open the request again.");
  if (!session) {
    const response = await fetch(input, options);
    if (hostedSession !== session) throw new Error("Your account changed. Open the request again.");
    return response;
  }
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

export function studioFetch(input: string, options: RequestInit = {}): Promise<Response> {
  const session = hostedSession;
  return scheduleRequest(input, options, () => executeStudioFetch(input, options, session));
}

export interface StudioUploadOptions {
  signal?: AbortSignal;
  /** Called once the request body finishes transmitting (or fails/aborts), before waiting on the response. */
  onUploadComplete?: () => void;
}

function parseXhrHeaders(raw: string): Headers {
  const headers = new Headers();
  for (const line of raw.split(/\r?\n/)) {
    const colon = line.indexOf(":");
    if (colon <= 0) continue;
    headers.append(line.slice(0, colon).trim(), line.slice(colon + 1).trim());
  }
  return headers;
}

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("Aborted", "AbortError");
}

async function executeStudioUpload(
  input: string,
  body: FormData,
  options: StudioUploadOptions,
  session: HostedApiSession | null,
): Promise<Response> {
  const signal = options.signal;
  let uploadNotified = false;
  const notifyUploadComplete = () => {
    if (uploadNotified) return;
    uploadNotified = true;
    options.onUploadComplete?.();
  };
  if (signal?.aborted) {
    notifyUploadComplete();
    throw abortError(signal);
  }
  if (!session || typeof XMLHttpRequest !== "function") {
    try {
      return await executeStudioFetch(input, { method: "POST", body, signal }, session);
    } finally {
      notifyUploadComplete();
    }
  }
  if (hostedSession !== session) {
    notifyUploadComplete();
    throw new Error("Your account changed. Open the request again.");
  }
  const url = new URL(input, session.origin);
  if (url.origin !== session.origin || !url.pathname.startsWith("/api/")) {
    notifyUploadComplete();
    throw new Error("Anteroom API requests must stay on this app's origin.");
  }
  let token: string | null;
  try {
    token = await session.accessToken();
  } catch (error) {
    notifyUploadComplete();
    throw error;
  }
  if (signal?.aborted) {
    notifyUploadComplete();
    throw abortError(signal);
  }
  if (!token || hostedSession !== session) {
    notifyUploadComplete();
    throw new Error("Sign in to continue.");
  }
  return new Promise<Response>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const finish = (cb: () => void) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", onAbort);
      notifyUploadComplete();
      cb();
    };
    const onAbort = () => {
      xhr.abort();
      finish(() => reject(abortError(signal!)));
    };
    if (signal) {
      if (signal.aborted) {
        finish(() => reject(abortError(signal)));
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }
    xhr.open("POST", url.href, true);
    xhr.withCredentials = false;
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload?.addEventListener?.("loadend", notifyUploadComplete, { once: true });
    xhr.onreadystatechange = () => {
      // An early error response (e.g. 429 or 413) can arrive before upload loadend.
      if (xhr.readyState >= 2) notifyUploadComplete();
    };
    xhr.onload = () => {
      finish(() => {
        if (hostedSession !== session) {
          reject(new Error("Your account changed. Open the request again."));
          return;
        }
        if (xhr.status === 401 && hostedSession === session) session.unauthorized();
        if (xhr.status < 200 || xhr.status > 599) {
          reject(new TypeError("Failed to fetch"));
          return;
        }
        const headers = parseXhrHeaders(xhr.getAllResponseHeaders?.() ?? "");
        const nullBody = xhr.status === 204 || xhr.status === 205 || xhr.status === 304;
        resolve(new Response(nullBody ? null : xhr.responseText, {
          status: xhr.status,
          statusText: xhr.statusText,
          headers,
        }));
      });
    };
    xhr.onerror = () => finish(() => reject(new TypeError("Failed to fetch")));
    xhr.ontimeout = () => finish(() => reject(new TypeError("Failed to fetch")));
    xhr.onabort = () => finish(() => reject(signal?.aborted ? abortError(signal) : new DOMException("Aborted", "AbortError")));
    try {
      xhr.send(body);
    } catch (error) {
      finish(() => reject(error));
    }
  });
}

/**
 * Post a multipart upload through the shared hosted auth and scheduler path,
 * calling `onUploadComplete` as soon as the request body finishes transmitting
 * so another item can start uploading while this request waits on inference.
 */
export function studioUpload(input: string, body: FormData, options: StudioUploadOptions = {}): Promise<Response> {
  const session = hostedSession;
  return scheduleRequest(input, { signal: options.signal }, () => executeStudioUpload(input, body, options, session));
}

/** Hold a single scheduler slot through the image body download. */
export function studioImage(input: string, signal: AbortSignal, session: HostedApiSession | null) {
  return scheduleRequest(input, { signal }, async () => {
    const response = await executeStudioFetch(input, { signal }, session);
    const blob = response.ok ? await response.blob() : null;
    if (hostedSession !== session) throw new Error("Your account changed.");
    return { status: response.status, blob };
  });
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
