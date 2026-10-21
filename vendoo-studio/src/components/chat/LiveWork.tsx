import { useState, type ReactNode } from "react";
import type { LiveAgents, LiveTraceEntry } from "../liveTrace";
import { TurnFoldRow, WorkingRow } from "./TimelineRows";
import { WorkLogEntries } from "./WorkLogEntries";
import { turnFoldLabel, type FoldedTurn } from "./workLogModel";

interface Props {
  busy: boolean;
  stopping: boolean;
  workStartedAtMs: number | null;
  streamTrace: LiveTraceEntry[];
  streamAgents: LiveAgents;
  /** This panel's own stream is running and has not failed. */
  localLive: boolean;
  tailLabel: string;
  renderPosted?: (id: string) => ReactNode;
}

/** The current turn's work, inline in the transcript under its "Working for" row (T3 Code style). */
export function LiveWork({ busy, stopping, workStartedAtMs, streamTrace, streamAgents, localLive, tailLabel, renderPosted }: Props) {
  return (
    <div className="pb-1.5" data-live-work>
      {busy ? <div className="pb-1.5"><WorkingRow startedAtMs={workStartedAtMs} stopping={stopping} /></div> : null}
      <WorkLogEntries
        trace={streamTrace}
        agents={streamAgents}
        live={localLive}
        renderPosted={renderPosted}
        tailLabel={tailLabel}
      />
    </div>
  );
}

/** A finished turn: one muted "Worked for 1m 3s ›" row that opens its work log. */
export function FoldedTurnRow({ turn }: { turn: FoldedTurn }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="pb-1.5" data-turn-fold={turn.id}>
      <TurnFoldRow
        label={turnFoldLabel(turn)}
        expanded={expanded}
        onToggle={() => setExpanded((value) => !value)}
        endedAt={new Date(turn.endedAtMs).toISOString()}
      />
      {expanded ? (
        <div className="pt-1.5">
          <WorkLogEntries trace={turn.trace} agents={turn.agents} live={false} />
        </div>
      ) : null}
    </div>
  );
}
