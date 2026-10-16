import type { Job } from "../../api/types";
import { isResumableGenerateFailure } from "./chatStream";

function streamErrorHint(activeProbe: Job | undefined, streamText: string): string {
  return activeProbe
    ? isResumableGenerateFailure(streamText || "")
      ? "Studio is still discovering fields in Chrome. Retry reconnects — use Cancel discovery only if Chrome is stuck."
      : `Active discovery job: ${activeProbe.current_step || activeProbe.status}. Cancel it here if Chrome is stuck, then retry.`
    : /connection dropped/i.test(streamText || "")
      ? "This is a client/network failure, not a model refusal. Retry resumes the same generate when Studio still has it running."
      : /timed out during|category selection timed out/i.test(streamText || "")
        ? "The listing model or Chrome step stalled. Retry generate. Cancel discovery only if the Listing tab shows a stuck probe."
      : /HTTP 5\d\d/i.test(streamText || "")
        ? "Studio hit an internal error before the model answered. Retry; if it keeps failing, check Studio logs or restart Studio."
        : /HTTP \d+/i.test(streamText || "")
          ? "The generate request was rejected before streaming started. Check Settings (provider sign-in / API key), then retry."
          : "";
}

/** A generate or send that ended in an error, with what to do next. */
export function StreamErrorPanel({
  streamText,
  activeProbe,
  busy,
  canRetry,
  onCancelDiscovery,
  onRetry,
}: {
  streamText: string;
  activeProbe: Job | undefined;
  busy: boolean;
  canRetry: boolean;
  onCancelDiscovery: () => Promise<void>;
  onRetry: () => Promise<void>;
}) {
  const errorHint = streamErrorHint(activeProbe, streamText);
  return (
    <div className="chat-error" role="alert">
      <div className="chat-error-text">{streamText}</div>
      {errorHint ? <div className="chat-error-hint">{errorHint}</div> : null}
      <div className="chat-error-actions">
        {activeProbe ? (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => { void onCancelDiscovery(); }}
            disabled={busy}
          >
            Cancel discovery
          </button>
        ) : null}
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => { void onRetry(); }}
          disabled={busy || !canRetry}
        >
          {busy ? "Retrying..." : activeProbe ? "Cancel & retry" : "Retry"}
        </button>
      </div>
    </div>
  );
}

/** The chat has messages but never produced a listing. */
export function UnfinishedGenerationPanel({
  probeActive,
  onCancelDiscovery,
  onRetry,
}: {
  probeActive: boolean;
  onCancelDiscovery: () => Promise<void>;
  onRetry: () => Promise<void>;
}) {
  return (
    <div className="chat-error">
      <div className="chat-error-text">Listing generation did not finish.</div>
      {probeActive ? (
        <div className="chat-error-hint">
          Field discovery is still running in Chrome. Cancel it from here, then retry.
        </div>
      ) : null}
      <div className="chat-error-actions">
        {probeActive ? (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => { void onCancelDiscovery(); }}
          >
            Cancel discovery
          </button>
        ) : null}
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => { void onRetry(); }}
        >
          {probeActive ? "Cancel & retry" : "Retry"}
        </button>
      </div>
    </div>
  );
}
