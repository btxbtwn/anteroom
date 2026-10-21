import { coalesceSseEvents, type SseParts } from "../sseCoalesce";
import { appendLiveTrace } from "../liveTrace";

export function detailFromResponseBody(body: unknown): string {
  if (!body || typeof body !== "object") return "";
  const detail = (body as { detail?: unknown; message?: unknown }).detail
    ?? (body as { message?: unknown }).message;
  if (typeof detail === "string") return detail.trim();
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) return String((item as { msg: unknown }).msg);
        return "";
      })
      .filter(Boolean)
      .join("; ");
  }
  return detail != null ? String(detail) : "";
}

export type StreamRequestError = Error & { httpStatus?: number; body?: unknown };

export function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as { name?: unknown }).name === "AbortError";
}

/** Turn opaque browser/HTTP failures into actionable chat errors. */
export function formatClientStreamError(
  source: unknown,
  opts: {
    action: "generate" | "send";
    httpStatus?: number;
    lastStatus?: string;
    probeActive?: boolean;
    probeStep?: string;
  },
): string {
  const raw = (() => {
    if (typeof source === "string") return source.trim();
    if (source instanceof Error) return source.message.trim();
    if (source && typeof source === "object") return detailFromResponseBody(source);
    return "";
  })().replace(/^Error:\s*/i, "");

  const actionLabel = opts.action === "send" ? "Chat request" : "Listing generation";
  const stage = (opts.lastStatus || "").trim();
  const stageBit = stage ? ` during “${stage}”` : "";
  const stageImpliesProbe = /identifying category|discovering.*(field|schema)|field discovery/i.test(stage);
  const probeActive = Boolean(opts.probeActive || stageImpliesProbe);
  const probeBit = probeActive
    ? ` Chrome is still on field discovery${opts.probeStep ? ` (${opts.probeStep})` : ""}.`
    : "";

  const network = /^(load failed|failed to fetch|networkerror when attempting to fetch resource|network request failed|the internet connection appears to be offline\.?)$/i.test(raw)
    || /failed to fetch|networkerror|load failed/i.test(raw);

  if (network) {
    return (
      `Error: ${actionLabel} connection dropped${stageBit}.${probeBit} `
      + (probeActive
        ? "Retry reconnects to the same generate — only Cancel discovery if Chrome is stuck."
        : "Retry to resume. If this keeps happening on mobile, use a stronger connection or desktop Studio.")
    );
  }

  if (opts.httpStatus) {
    const detail = raw || "Request failed";
    return `Error: ${actionLabel} failed (HTTP ${opts.httpStatus})${stageBit}: ${detail}.${probeBit}`.trim();
  }

  if (!raw) {
    return `Error: ${actionLabel} failed${stageBit}.${probeBit} Retry, or cancel discovery if Chrome is stuck.`.trim();
  }

  if (/^error:/i.test(raw)) return raw.startsWith("Error:") ? raw : `Error: ${raw}`;
  return `Error: ${raw}`;
}

/**
 * Events that only feed the live trace and never belong in the reply text.
 * `agent` carries JSON state for parallel generation work; it is ignored here
 * until the chat renders it.
 */
export const TRACE_ONLY_EVENTS = new Set(["step", "posted", "agent"]);

type SseParseState = { eventType: string; parts: SseParts; dataLines: number };

export const SSE_FETCH: RequestInit = {
  method: "POST",
  headers: { Accept: "text/event-stream" },
  cache: "no-store",
};

function applySseLine(
  raw: string,
  state: SseParseState,
  onEvent: (event: string, parts: SseParts) => void,
): boolean {
  const line = raw.replace(/\r$/, "");
  if (!line) {
    state.eventType = "message";
    state.dataLines = 0;
    return false;
  }
  if (line.startsWith(":")) return false;
  if (line.startsWith("event:")) {
    state.eventType = line.slice(6).trim() || "message";
    return false;
  }
  if (!line.startsWith("data:")) return false;
  const chunk = line.startsWith("data: ") ? line.slice(6) : line.slice(5);
  if (chunk === "[DONE]") {
    state.eventType = "message";
    return true;
  }
  // Data lines within one event are joined with newlines (the server splits multi-line text this way).
  const continued = state.dataLines++ > 0;
  const text = continued ? `\n${chunk}` : chunk;
  state.parts.trace = appendLiveTrace(state.parts.trace, state.eventType, text, continued);
  if (state.eventType === "thinking") state.parts.thinking += text;
  else if (state.eventType === "status") state.parts.status = continued ? state.parts.status + text : text;
  else if (!TRACE_ONLY_EVENTS.has(state.eventType)) state.parts.content += text;
  onEvent(state.eventType, state.parts);
  return false;
}

function consumeSseText(
  text: string,
  onEvent: (event: string, parts: SseParts) => void,
): { parts: SseParts; sawDone: boolean } {
  const state: SseParseState = { eventType: "message", parts: { content: "", thinking: "", status: "", trace: [] }, dataLines: 0 };
  let sawDone = false;
  const batch = coalesceSseEvents(onEvent);
  try {
    for (const line of text.split("\n")) {
      if (applySseLine(line, state, (event, parts) => batch.push(event, parts))) sawDone = true;
    }
  } finally {
    batch.flush();
  }
  return { parts: state.parts, sawDone };
}

async function consumeSse(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (event: string, parts: SseParts) => void,
): Promise<{ parts: SseParts; sawDone: boolean }> {
  const decoder = new TextDecoder();
  let buffer = "";
  let sawDone = false;
  const state: SseParseState = { eventType: "message", parts: { content: "", thinking: "", status: "", trace: [] }, dataLines: 0 };
  const batch = coalesceSseEvents(onEvent);
  const emit = (event: string, parts: SseParts) => batch.push(event, parts);
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const raw of lines) {
        if (applySseLine(raw, state, emit)) sawDone = true;
      }
    }
    if (buffer && applySseLine(buffer, state, emit)) sawDone = true;
  } finally {
    // The caller flips streaming off right after this returns, so the last
    // batch has to land first -- including when the read throws or aborts.
    batch.flush();
  }
  return { parts: state.parts, sawDone };
}

export async function consumeResponseSse(
  res: Response,
  onEvent: (event: string, parts: SseParts) => void,
): Promise<{ parts: SseParts; sawDone: boolean }> {
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    reader = res.body?.getReader();
  } catch {
    reader = undefined;
  }
  if (reader) return consumeSse(reader, onEvent);
  return consumeSseText(await res.text(), onEvent);
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function isResumableGenerateFailure(text: string): boolean {
  return /connection dropped|did not finish|reconnecting/i.test(text);
}
