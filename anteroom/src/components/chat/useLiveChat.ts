import { useEffect, useState } from "react";
import { getLive } from "./liveChatStore";

function snapshot(convId: string) {
  const live = getLive(convId);
  return {
    streaming: live.streaming,
    generating: live.generating,
    streamText: live.streamDisplayText,
    /** Everything received so far, before it is cut to whole words for display. */
    streamReceived: live.streamText,
    streamTrace: live.streamTrace,
    streamAgents: live.streamAgents,
    foldedTurns: live.foldedTurns,
    streamStatus: live.streamStatus,
    resetting: live.resetting,
    workStartedAtMs: live.workStartedAtMs,
    failedAction: live.failedAction,
    lastSendText: live.lastSendText,
  };
}

/** This listing's live stream, mirrored into state so the panel re-renders as it moves. */
export function useLiveChat(convId: string) {
  const [state, setState] = useState(() => ({ convId, data: snapshot(convId) }));

  useEffect(() => {
    const sync = () => setState((current) => {
      const next = snapshot(convId);
      // Bail out when nothing the panel reads changed.
      const same = current.convId === convId
        && (Object.keys(next) as (keyof typeof next)[]).every((key) => Object.is(next[key], current.data[key]));
      return same ? current : { convId, data: next };
    });
    sync();
    getLive(convId).listeners.add(sync);
    return () => {
      getLive(convId).listeners.delete(sync);
    };
  }, [convId]);

  return state.convId === convId ? state.data : snapshot(convId);
}
