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
  const token = await raceAbort(session.accessToken(), options.signal ?? undefined);
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
  /** Pin the upload to the session captured when the request was queued. */
  session?: HostedApiSession | null;
  /** Abort a stalled upload body after this many ms without progress; cleared once body upload completes. */
  uploadTimeoutMs?: number;
  /** Called once the request body finishes transmitting (or fails/aborts), before waiting on the response. */
  onUploadComplete?: () => void;
}

const DEFAULT_UPLOAD_PHASE_TIMEOUT_MS = 60_000;
const RESPONSE_BODY_IDLE_TIMEOUT_MS = 60_000;

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

function raceAbort<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) {
    promise.catch(() => {});
    return Promise.reject(abortError(signal));
  }
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError(signal));
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

async function executeStudioUpload(
  input: string,
  body: FormData,
  options: StudioUploadOptions,
  session: HostedApiSession | null,
): Promise<Response> {
  const signal = options.signal;
  const uploadTimeoutMs = options.uploadTimeoutMs ?? DEFAULT_UPLOAD_PHASE_TIMEOUT_MS;
  let uploadNotified = false;
  let clearUploadTimer = () => {};
  const notifyUploadComplete = () => {
    clearUploadTimer();
    if (uploadNotified) return;
    uploadNotified = true;
    options.onUploadComplete?.();
  };
  if (signal?.aborted) {
    notifyUploadComplete();
    throw abortError(signal);
  }
  if (hostedSession !== session) {
    notifyUploadComplete();
    throw new Error("Your account changed. Open the request again.");
  }
  if (!session || typeof XMLHttpRequest !== "function") {
    try {
      return await executeStudioFetch(input, { method: "POST", body, signal }, session);
    } finally {
      notifyUploadComplete();
    }
  }
  const url = new URL(input, session.origin);
  if (url.origin !== session.origin || !url.pathname.startsWith("/api/")) {
    notifyUploadComplete();
    throw new Error("Anteroom API requests must stay on this app's origin.");
  }
  let token: string | null;
  try {
    token = await raceAbort(session.accessToken(), signal);
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
    let uploadTimer: ReturnType<typeof setTimeout> | null = null;
    let lastUploadedBytes = 0;
    let responseTimer: ReturnType<typeof setTimeout> | undefined;
    let lastDownloadedBytes = 0;
    clearUploadTimer = () => {
      if (uploadTimer !== null) {
        clearTimeout(uploadTimer);
        uploadTimer = null;
      }
    };
    const finish = (cb: () => void) => {
      if (settled) return;
      settled = true;
      clearUploadTimer();
      clearTimeout(responseTimer);
      signal?.removeEventListener("abort", onAbort);
      notifyUploadComplete();
      cb();
    };
    const armUploadTimer = () => {
      if (uploadNotified || settled || uploadTimeoutMs <= 0 || !Number.isFinite(uploadTimeoutMs)) return;
      clearUploadTimer();
      uploadTimer = setTimeout(() => {
        if (uploadNotified || settled) return;
        finish(() => reject(new TypeError("Failed to fetch")));
        xhr.abort();
      }, uploadTimeoutMs);
    };
    const armResponseTimer = () => {
      if (settled) return;
      clearTimeout(responseTimer);
      responseTimer = setTimeout(() => {
        finish(() => reject(new TypeError("Failed to fetch")));
        xhr.abort();
      }, RESPONSE_BODY_IDLE_TIMEOUT_MS);
    };
    const onAbort = () => {
      finish(() => reject(abortError(signal!)));
      xhr.abort();
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
    xhr.upload?.addEventListener?.("progress", (event) => {
      const loaded = typeof (event as ProgressEvent)?.loaded === "number" ? (event as ProgressEvent).loaded : 0;
      if (loaded > lastUploadedBytes) {
        lastUploadedBytes = loaded;
        armUploadTimer();
      }
    });
    xhr.upload?.addEventListener?.("loadend", notifyUploadComplete, { once: true });
    xhr.onreadystatechange = () => {
      // An early error response (e.g. 429 or 413) can arrive before upload loadend.
      if (xhr.readyState >= 2) notifyUploadComplete();
      // Inference can run before headers; only bound the wait for the response body.
      if (xhr.readyState === 2) armResponseTimer();
    };
    xhr.onprogress = ({ loaded }) => {
      if (responseTimer !== undefined && loaded > lastDownloadedBytes) {
        lastDownloadedBytes = loaded;
        armResponseTimer();
      }
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
    armUploadTimer();
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
  const session = options.session !== undefined ? options.session : hostedSession;
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
