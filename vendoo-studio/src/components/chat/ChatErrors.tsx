import { CircleAlertIcon } from "lucide-react";
import type { Job } from "../../api/types";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "../ui/alert";
import { Button } from "../ui/button";
import { isResumableGenerateFailure } from "./chatStream";

function streamErrorHint(activeProbe: Job | undefined, streamText: string): string {
  return activeProbe
    ? isResumableGenerateFailure(streamText || "")
      ? "Anteroom is still discovering fields in Chrome. Retry reconnects — use Cancel discovery only if Chrome is stuck."
      : `Active discovery job: ${activeProbe.current_step || activeProbe.status}. Cancel it here if Chrome is stuck, then retry.`
    : /connection dropped/i.test(streamText || "")
      ? "This is a client/network failure, not a model refusal. Retry resumes the same generate when Anteroom still has it running."
      : /timed out during|category selection timed out/i.test(streamText || "")
        ? "The listing model or Chrome step stalled. Retry generate. Cancel discovery only if the Listing tab shows a stuck probe."
      : /HTTP 5\d\d/i.test(streamText || "")
        ? "Anteroom hit an internal error before the model answered. Retry; if it keeps failing, check Anteroom logs or restart Anteroom."
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
    <Alert variant="error" role="alert" className="my-2">
      <CircleAlertIcon />
      <AlertTitle className="whitespace-pre-wrap break-words">{streamText}</AlertTitle>
      {errorHint ? <AlertDescription>{errorHint}</AlertDescription> : null}
      <AlertAction>
        {activeProbe ? (
          <Button type="button" size="xs" variant="outline" onClick={() => { void onCancelDiscovery(); }} disabled={busy}>
            Cancel discovery
          </Button>
        ) : null}
        <Button type="button" size="xs" onClick={() => { void onRetry(); }} disabled={busy || !canRetry}>
          {busy ? "Retrying..." : activeProbe ? "Cancel & retry" : "Retry"}
        </Button>
      </AlertAction>
    </Alert>
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
    <Alert variant="error" className="my-2">
      <CircleAlertIcon />
      <AlertTitle>Listing generation did not finish.</AlertTitle>
      {probeActive ? (
        <AlertDescription>Field discovery is still running in Chrome. Cancel it from here, then retry.</AlertDescription>
      ) : null}
      <AlertAction>
        {probeActive ? (
          <Button type="button" size="xs" variant="outline" onClick={() => { void onCancelDiscovery(); }}>
            Cancel discovery
          </Button>
        ) : null}
        <Button type="button" size="xs" onClick={() => { void onRetry(); }}>
          {probeActive ? "Cancel & retry" : "Retry"}
        </Button>
      </AlertAction>
    </Alert>
  );
}
