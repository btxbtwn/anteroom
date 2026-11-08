import { studioFetch } from "./transport";

/** Uncaught browser errors go to /api/error-reports; hosted emails them to the operator. */
type FailureKind = "crash" | "unhandled-rejection" | "render";

const PER_PAGE_LIMIT = 20;
const REPEAT_MS = 60_000;
const recent = new Map<string, number>();
let sent = 0;

export function describeError(kind: FailureKind, error: unknown) {
  const value = error instanceof Error ? error : null;
  return {
    kind,
    name: value?.name ?? (error === undefined ? null : typeof error),
    message: (value?.message ?? (typeof error === "string" ? error : safeString(error))).slice(0, 4000),
    stack: value?.stack?.slice(0, 16000) ?? null,
    // Pathname only: a query or hash can carry sign-in codes.
    path: typeof location === "undefined" ? "-" : location.pathname.slice(0, 500),
    user_agent: typeof navigator === "undefined" ? null : navigator.userAgent.slice(0, 500),
    release: __STUDIO_BUILD__.version,
  };
}

function safeString(value: unknown) {
  try { return JSON.stringify(value) ?? String(value); } catch { return String(value); }
}

export function reportError(kind: FailureKind, error: unknown) {
  const report = describeError(kind, error);
  const key = `${kind}:${report.message}`;
  const now = Date.now();
  if (sent >= PER_PAGE_LIMIT || now - (recent.get(key) ?? -Infinity) < REPEAT_MS) return;
  recent.set(key, now);
  sent += 1;
  void studioFetch("/api/error-reports", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(report), signal: AbortSignal.timeout(5000), keepalive: true,
  }).catch(() => { /* Reporting must never recurse or interrupt work. */ });
}

export function installErrorReports() {
  window.addEventListener("error", event => reportError("crash", event.error ?? event.message));
  window.addEventListener("unhandledrejection", event => reportError("unhandled-rejection", event.reason));
}
