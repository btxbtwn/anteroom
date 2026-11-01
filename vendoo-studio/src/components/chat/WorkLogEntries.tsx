/**
 * A turn's work log, live or folded: phases, grouped steps, reasoning and
 * subagent groups, drawn with T3 Code's WorkLog rows.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.): LiveActivityRow,
 * ActivityShimmerOverlay, WorkGroupHeader, ReasoningTraceContent and
 * V2SubagentGroup in components/chat/MessagesTimeline.tsx, SubagentTimelineLink
 * in components/chat/V2LifecycleRow.tsx and AgentElapsed.tsx.
 */
import { memo, useEffect, useRef, useState, type ReactNode } from "react";
import {
  BotIcon,
  BrainIcon,
  CheckIcon,
  ChevronDownIcon,
  CircleCheckIcon,
  FileTextIcon,
  GlobeIcon,
  ListIcon,
  LoaderCircleIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react";

import { cn } from "../../lib/utils";
import { observeVisibleAnimation } from "../../lib/visibleAnimation";
import { CHAT_PROSE_CLASS, ChatMarkdown } from "../ChatMarkdown";
import { settledPhaseText, thoughtPreview, type LiveAgent, type LiveAgents, type LiveTraceEntry } from "../liveTrace";
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from "../ui/collapsible";
import { WorkLogBlock, WorkLogButton, WorkLogDetails, WorkLogList, WorkLogRow } from "./WorkLog";
import {
  agentElapsedMs,
  agentGroupTiming,
  agentsFor,
  deriveAgentSpawnSummary,
  formatAgentElapsed,
  groupWorkLog,
  stepAction,
  summarizeAgentStatuses,
  summarizeSteps,
  type WorkLogItem,
} from "./workLogModel";

// ── Live activity ───────────────────────────────────────────────────────

/** T3's moving highlight over a running row's label. */
function ActivityShimmerOverlay({ children }: { children: ReactNode }) {
  return (
    <span aria-hidden className="live-activity-focus pointer-events-none absolute inset-y-0 select-none">
      <span className="live-activity-focus-counter block">
        <span className="live-activity-focus-aligned block text-foreground">{children}</span>
      </span>
    </span>
  );
}

function RowIcon({ children, highlighted = false, failed = false }: { children: ReactNode; highlighted?: boolean; failed?: boolean }) {
  return (
    <span
      className={cn(
        "flex size-4 items-center justify-center [&_svg]:block [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:stroke-2",
        failed ? "text-tool-error-icon/40" : highlighted ? "text-foreground" : "text-icon-muted",
      )}
    >
      {children}
    </span>
  );
}

/** A running row: the label shines, or a shimmer passes over the whole row. */
function LiveActivityRow({
  label,
  icon,
  shimmer = false,
}: {
  label: ReactNode;
  icon: ReactNode;
  shimmer?: boolean;
}) {
  const content = (highlighted: boolean, shine: boolean) => (
    <WorkLogRow
      icon={<RowIcon highlighted={highlighted}>{icon}</RowIcon>}
      label={<span className={cn("block truncate", highlighted && "text-foreground", shine && "live-tool-shine")}>{label}</span>}
    />
  );
  return (
    <div
      ref={observeVisibleAnimation}
      className="relative min-h-6 w-fit max-w-full min-w-0 overflow-hidden rounded-md text-sm leading-relaxed"
    >
      {content(false, !shimmer)}
      {shimmer ? <ActivityShimmerOverlay>{content(true, false)}</ActivityShimmerOverlay> : null}
    </div>
  );
}

// ── Steps ───────────────────────────────────────────────────────────────

function stepIcon(text: string): ReactNode {
  switch (stepAction(text)) {
    case "search":
      return <GlobeIcon />;
    case "found":
      return <ListIcon />;
    case "schema":
    case "read":
      return <FileTextIcon />;
    case "failed":
      return <XIcon />;
    case "other":
      return <WrenchIcon />;
  }
}

function StepRow({ text }: { text: string }) {
  const failed = stepAction(text) === "failed";
  return (
    <WorkLogRow
      icon={<RowIcon failed={failed}>{stepIcon(text)}</RowIcon>}
      label={<span className={cn("block truncate", failed && "text-destructive-foreground")} title={text}>{text}</span>}
    />
  );
}

function StepGroup({ texts, live }: { texts: string[]; live: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const { summary, hasFailure } = summarizeSteps(texts);
  return (
    <>
      <WorkLogButton
        ref={live ? observeVisibleAnimation : undefined}
        aria-label={hasFailure ? `${summary}, a step failed` : summary}
        aria-expanded={expanded}
        data-scroll-anchor-ignore
        onClick={() => setExpanded((value) => !value)}
        icon={<RowIcon>{stepIcon(texts[texts.length - 1])}</RowIcon>}
        label={<span className={cn("block truncate", live && !hasFailure && "live-tool-shine")}>{summary}</span>}
      />
      {expanded ? (
        <WorkLogList>
          {texts.map((text, index) => <StepRow key={`${index}-${text}`} text={text} />)}
        </WorkLogList>
      ) : null}
    </>
  );
}

// ── Reasoning ───────────────────────────────────────────────────────────

function ThoughtRow({ text, live }: { text: string; live: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const preview = thoughtPreview(text);
  const label = live ? "Thinking" : preview || "Thought";
  return (
    <WorkLogRow
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={() => setExpanded((value) => !value)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        setExpanded((value) => !value);
      }}
      icon={<RowIcon highlighted={live}><BrainIcon /></RowIcon>}
      label={
        live ? (
          <span ref={observeVisibleAnimation} className="relative block truncate">
            <span className="live-tool-shine">{label}</span>
            {preview ? <span className="ms-1.5 text-muted-foreground/70">{preview}</span> : null}
          </span>
        ) : (
          <span className="block truncate" title={preview}>{label}</span>
        )
      }
    >
      {expanded ? (
        <WorkLogDetails followLive={live}>
          <ChatMarkdown text={text} isStreaming={live} lineBreaks className={cn(CHAT_PROSE_CLASS, "text-foreground")} />
        </WorkLogDetails>
      ) : null}
    </WorkLogRow>
  );
}

// ── Subagents ───────────────────────────────────────────────────────────

/** Ticks once a second by writing textContent, so the row never re-renders for the clock. */
export function AgentElapsed({ startedAt, endedAt }: { startedAt: number; endedAt: number | null }) {
  const textRef = useRef<HTMLSpanElement>(null);
  const live = endedAt === null;
  const [mountedAt] = useState(() => Date.now());
  useEffect(() => {
    const update = () => {
      const text = formatAgentElapsed(agentElapsedMs({ startedAt, endedAt }, Date.now()) / 1000);
      if (textRef.current && textRef.current.textContent !== text) textRef.current.textContent = text;
    };
    update();
    if (!live) return;
    const id = window.setInterval(update, 1000);
    return () => window.clearInterval(id);
  }, [live, startedAt, endedAt]);
  return (
    <span ref={textRef} className="tabular-nums">
      {formatAgentElapsed(agentElapsedMs({ startedAt, endedAt }, mountedAt) / 1000)}
    </span>
  );
}

const STATUS_VISUALS: Record<LiveAgent["status"], { dotClass: string; label: string }> = {
  running: { dotClass: "bg-info", label: "Running" },
  done: { dotClass: "bg-success", label: "Completed" },
  failed: { dotClass: "bg-destructive", label: "Failed" },
  cancelled: { dotClass: "bg-muted-foreground/60", label: "Stopped" },
};

function AgentAvatar({ status }: { status?: LiveAgent["status"] }) {
  return (
    <span
      aria-hidden
      className="relative inline-flex size-6 shrink-0 items-center justify-center rounded-full border border-border/70 bg-muted ring-2 ring-background"
    >
      <BotIcon className="size-3.5 text-muted-foreground" />
      {status ? (
        <span
          className={cn(
            "absolute -right-px -bottom-px size-2 rounded-full ring-2 ring-background",
            STATUS_VISUALS[status].dotClass,
            status === "running" && "motion-safe:animate-pulse",
          )}
        />
      ) : null}
    </span>
  );
}

function AgentStatusIcon({ status }: { status: LiveAgent["status"] }) {
  if (status === "running") {
    return (
      <svg
        ref={observeVisibleAnimation}
        viewBox="0 0 24 24"
        aria-hidden
        className="size-3.5 shrink-0 text-info motion-safe:visible-animate-spin"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.25"
        strokeDasharray="3.5 3"
        strokeLinecap="round"
      >
        <circle cx="12" cy="12" r="9" />
      </svg>
    );
  }
  if (status === "done") return <CheckIcon aria-hidden className="size-3.5 shrink-0 text-success" />;
  if (status === "failed") return <XIcon aria-hidden className="size-3.5 shrink-0 text-destructive" />;
  return <span aria-hidden className="size-2.5 shrink-0 rounded-[2px] bg-muted-foreground/60" />;
}

function AgentMemberRow({ agent }: { agent: LiveAgent }) {
  const failed = agent.status === "failed";
  const statusLabel = STATUS_VISUALS[agent.status].label;
  return (
    <div
      data-agent-id={agent.id}
      aria-description={statusLabel}
      title={agent.detail ?? statusLabel}
      className="group/subagent flex w-full min-w-0 items-center gap-2.5 rounded-md px-2 py-1.5 text-left"
    >
      <AgentStatusIcon status={agent.status} />
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline gap-2">
          <span className="min-w-0 truncate text-xs font-medium text-foreground">{agent.label}</span>
          {agent.detail !== null && agent.status !== "done" ? (
            <span className={cn("shrink-0 text-3xs", failed ? "text-destructive-foreground" : "text-muted-foreground")}>
              {statusLabel}
            </span>
          ) : null}
        </span>
        <span className={cn("block text-2xs leading-relaxed", failed ? "text-destructive-foreground" : "text-muted-foreground")}>
          <span className="block truncate">{agent.detail ?? statusLabel}</span>
        </span>
      </span>
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
        <AgentElapsed startedAt={agent.startedAt} endedAt={agent.endedAt} />
      </span>
    </div>
  );
}

/** "Kicked off 3 subagents · 2 working", collapsible to one row per agent. */
export const SubagentGroup = memo(function SubagentGroup({ agents }: { agents: LiveAgent[] }) {
  const spawn = deriveAgentSpawnSummary(agents);
  // Open while they work, like T3's running phases; the seller's own toggle wins.
  const [toggled, setToggled] = useState<boolean | null>(null);
  const expanded = toggled ?? spawn.live;
  const statusSummary = spawn.live ? summarizeAgentStatuses(agents) : spawn.status;
  const timing = agentGroupTiming(agents);
  return (
    <Collapsible open={expanded} onOpenChange={(open) => setToggled(open)} data-subagent-group>
      <CollapsibleTrigger
        aria-label={spawn.lead}
        aria-description={statusSummary}
        data-scroll-anchor-ignore
        className={cn(
          "flex w-full min-w-0 items-center gap-3 py-2 text-left transition-opacity hover:opacity-100",
          expanded || spawn.live ? "text-foreground opacity-100" : "text-muted-foreground opacity-55",
        )}
      >
        <span className="flex shrink-0 items-center -space-x-1.5" aria-hidden>
          {agents.slice(0, 3).map((agent) => (
            <AgentAvatar key={agent.id} status={agents.length === 1 ? agent.status : undefined} />
          ))}
          {agents.length > 3 ? (
            <span className="relative inline-flex size-6 items-center justify-center rounded-full bg-muted text-3xs font-medium text-muted-foreground ring-2 ring-background">
              +{agents.length - 3}
            </span>
          ) : null}
        </span>
        <span className="min-w-0 flex-1">
          <span ref={spawn.live ? observeVisibleAnimation : undefined} className="block text-xs font-semibold">
            <span className={cn(spawn.live && "live-tool-shine")}>{spawn.lead}</span>
          </span>
          <span
            className={cn(
              "block truncate text-3xs text-muted-foreground",
              spawn.tone === "working" ? "text-info" : spawn.tone === "failed" && "text-destructive-foreground",
            )}
          >
            {statusSummary}
          </span>
        </span>
        {timing ? (
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
            <AgentElapsed startedAt={timing.startedAt} endedAt={timing.endedAt} />
          </span>
        ) : null}
        <ChevronDownIcon
          aria-hidden
          className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform", expanded && "rotate-180")}
        />
      </CollapsibleTrigger>
      <CollapsiblePanel animate={false}>
        {expanded ? (
          <div className="mt-1 mb-1 rounded-lg border border-border/60 bg-card/30 p-1">
            {agents.map((agent) => <AgentMemberRow key={agent.id} agent={agent} />)}
          </div>
        ) : null}
      </CollapsiblePanel>
    </Collapsible>
  );
});

// ── The log ─────────────────────────────────────────────────────────────

export interface WorkLogEntriesProps {
  trace: LiveTraceEntry[];
  agents: LiveAgents;
  /** The run is still going: the newest phase and thought are live. */
  live: boolean;
  /** Draws a card the run posted (evidence, comps) where it happened. */
  renderPosted?: (id: string) => ReactNode;
  /** A row naming what is happening before the first phase arrives. */
  tailLabel?: string;
}

export function WorkLogEntries({ trace, agents, live, renderPosted, tailLabel }: WorkLogEntriesProps) {
  const items = groupWorkLog(trace);
  const lastIndex = trace.length - 1;
  let livePhaseIndex = -1;
  if (live) {
    for (let index = lastIndex; index >= 0; index -= 1) {
      if (trace[index].kind === "phase") {
        livePhaseIndex = index;
        break;
      }
    }
  }
  const thinkingNow = live && trace[lastIndex]?.kind === "thought";
  const lastItem = items[items.length - 1];

  const render = (item: WorkLogItem): ReactNode => {
    switch (item.kind) {
      case "posted":
        return renderPosted ? <div key={`posted-${item.id}`} className="py-1.5">{renderPosted(item.id)}</div> : null;
      case "thought":
        return <WorkLogBlock key={`thought-${item.index}`}><ThoughtRow text={item.text} live={live && item.index === lastIndex} /></WorkLogBlock>;
      case "step":
        return <WorkLogBlock key={`step-${item.index}`}><StepRow text={item.text} /></WorkLogBlock>;
      case "steps":
        return (
          <WorkLogBlock key={`steps-${item.index}`}>
            <StepGroup texts={item.texts} live={live && item === lastItem} />
          </WorkLogBlock>
        );
      case "agents":
        return (
          <WorkLogBlock key={`agents-${item.index}`}>
            <SubagentGroup agents={agentsFor(item.ids, agents)} />
          </WorkLogBlock>
        );
      case "phase": {
        const running = item.index === livePhaseIndex;
        return (
          <WorkLogBlock key={`phase-${item.index}`}>
            {running ? (
              <LiveActivityRow
                label={item.text}
                icon={<LoaderCircleIcon className="motion-safe:visible-animate-spin" />}
                shimmer={!thinkingNow}
              />
            ) : (
              <WorkLogRow
                icon={<RowIcon><CircleCheckIcon /></RowIcon>}
                label={<span className="block truncate" title={item.text}>{settledPhaseText(item.text)}</span>}
              />
            )}
          </WorkLogBlock>
        );
      }
    }
  };

  return (
    <div className="flex min-w-0 flex-col">
      {items.map(render)}
      {tailLabel ? (
        <WorkLogBlock>
          <LiveActivityRow label={tailLabel} icon={<LoaderCircleIcon className="motion-safe:visible-animate-spin" />} shimmer />
        </WorkLogBlock>
      ) : null}
    </div>
  );
}
