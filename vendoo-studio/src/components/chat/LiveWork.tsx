import type { Message } from "../../api/types";
import { WorkingDuration } from "../WorkingDuration";
import { settledPhaseText, type LiveTraceEntry } from "../liveTrace";
import { ChatMessage } from "./ChatMessage";
import { LiveActivityRow, LiveThought, PhaseIcon, SearchIcon } from "./LiveActivity";

interface Props {
  busy: boolean;
  stopping: boolean;
  workStartedAtMs: number | null;
  streamTrace: LiveTraceEntry[];
  messages: Message[] | undefined;
  /** This panel's own stream is running and has not failed. */
  localLive: boolean;
  livePhase: number;
  tailLabel: string;
}

/** The current turn's work, inline in the transcript under its "Working for" header. */
export function LiveWork({ busy, stopping, workStartedAtMs, streamTrace, messages, localLive, livePhase, tailLabel }: Props) {
  // T3 Code's turn header: "Working for Xs" sits in the transcript above the
  // turn's work, and what is running now is the shimmering row below it.
  const workingHeader = stopping ? (
    workStartedAtMs != null ? (
      <>Stopping — <WorkingDuration startedAtMs={workStartedAtMs} className="chat-activity-duration" /></>
    ) : "Stopping…"
  ) : workStartedAtMs != null ? (
    <>Working for <WorkingDuration startedAtMs={workStartedAtMs} className="chat-activity-duration" /></>
  ) : "Working…";
  const lastTraceIndex = streamTrace.length - 1;
  function renderTraceEntry(entry: LiveTraceEntry, index: number) {
    if (entry.kind === "posted") {
      const message = messages?.find((m) => String(m.id) === entry.id);
      return message ? <ChatMessage key={message.id} message={message} /> : null;
    }
    if (entry.kind === "thought") {
      return <LiveThought key={`thought-${index}`} text={entry.text} live={localLive && index === lastTraceIndex} />;
    }
    if (entry.kind === "agents") return null;
    if (entry.kind === "step") {
      return <LiveActivityRow key={`step-${index}`} icon={<SearchIcon />} label={entry.text} live={false} />;
    }
    const live = index === livePhase;
    // One moving highlight at a time: a live thought under this phase has it.
    const thinkingNow = localLive && streamTrace[lastTraceIndex]?.kind === "thought";
    return (
      <LiveActivityRow
        key={`phase-${index}`}
        icon={<PhaseIcon live={live} />}
        label={live ? entry.text : settledPhaseText(entry.text)}
        live={live && !thinkingNow}
      />
    );
  }
  return (
    <div className="live-work" role="status" aria-live="polite">
      {busy ? <div className="live-work-header">{workingHeader}</div> : null}
      {streamTrace.map(renderTraceEntry)}
      {tailLabel ? <LiveActivityRow icon={<PhaseIcon live />} label={tailLabel} live /> : null}
    </div>
  );
}
