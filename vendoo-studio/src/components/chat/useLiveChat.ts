import { useEffect, useState } from "react";
import { getLive } from "./liveChatStore";

/** This listing's live stream, mirrored into state so the panel re-renders as it moves. */
export function useLiveChat(convId: string) {
  const live = getLive(convId);
  const [streaming, setStreaming] = useState(live.streaming);
  const [generating, setGenerating] = useState(live.generating);
  const [streamText, setStreamText] = useState(live.streamDisplayText);
  const [streamTrace, setStreamTrace] = useState(live.streamTrace);
  const [streamStatus, setStreamStatus] = useState(live.streamStatus);
  const [resetting, setResetting] = useState(live.resetting);
  const [workStartedAtMs, setWorkStartedAtMs] = useState(live.workStartedAtMs);
  const [failedAction, setFailedAction] = useState<"generate" | "send" | null>(live.failedAction);
  const [lastSendText, setLastSendText] = useState(live.lastSendText);

  useEffect(() => {
    const sync = () => {
      const next = getLive(convId);
      setStreaming(next.streaming);
      setGenerating(next.generating);
      setStreamText(next.streamDisplayText);
      setStreamTrace(next.streamTrace);
      setStreamStatus(next.streamStatus);
      setResetting(next.resetting);
      setWorkStartedAtMs(next.workStartedAtMs);
      setFailedAction(next.failedAction);
      setLastSendText(next.lastSendText);
    };
    sync();
    getLive(convId).listeners.add(sync);
    return () => {
      getLive(convId).listeners.delete(sync);
    };
  }, [convId]);

  return {
    streaming,
    generating,
    streamText,
    streamTrace,
    streamStatus,
    resetting,
    workStartedAtMs,
    failedAction,
    lastSendText,
  };
}
