import React, { useState, useRef, useEffect } from "react";
import { parseThinkingTodos, type ThinkingTodo } from "../thinkingTodos";
import { thoughtPreview } from "../liveTrace";

function thinkingItemClass(item: ThinkingTodo, live: boolean): string {
  return [
    item.done ? "is-done" : "",
    item.current ? "is-current" : "",
    item.current && live ? "is-live" : "",
  ].filter(Boolean).join(" ");
}

function ThinkingStreamBody({
  text,
  finished,
}: {
  text: string;
  finished: boolean;
}) {
  const items = parseThinkingTodos(text, finished);
  const live = !finished;
  if (items.length > 0 && items.every((item) => item.prose)) {
    return (
      <div className="thinking-prose">
        {items.map((item, index) => (
          <p
            key={`${index}-${item.text.slice(0, 24)}`}
            className={["thinking-prose-line", thinkingItemClass(item, live)].filter(Boolean).join(" ")}
          >
            {item.text}
          </p>
        ))}
      </div>
    );
  }
  return (
    <ul className="thinking-todos">
      {items.map((item, index) => (
        <li
          key={`${index}-${item.text}`}
          className={["thinking-todo", thinkingItemClass(item, live)].filter(Boolean).join(" ")}
        >
          <span className="thinking-todo-mark">{item.done ? "- [x]" : "- [ ]"}</span>
          <span className="thinking-todo-text">{item.text}</span>
        </li>
      ))}
    </ul>
  );
}

function BrainIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9.5 4.5A3 3 0 0 0 6 7.46 3.5 3.5 0 0 0 5.5 14 3 3 0 0 0 9 18.5" />
      <path d="M14.5 4.5A3 3 0 0 1 18 7.46a3.5 3.5 0 0 1 .5 6.54 3 3 0 0 1-3.5 4.5" />
      <path d="M9.5 4.5A2.5 2.5 0 0 1 12 2v20a2.5 2.5 0 0 1-2.5-2.5M14.5 4.5A2.5 2.5 0 0 0 12 2M15 9.5a3 3 0 0 1 3-2M9 9.5a3 3 0 0 0-3-2M15 14.5a3 3 0 0 0 3 2M9 14.5a3 3 0 0 1-3 2" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

export function PhaseIcon({ live }: { live: boolean }) {
  return live ? (
    <svg className="live-phase-spinner" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <path d="M12 3a9 9 0 1 0 9 9" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </svg>
  );
}

/** Label with T3 Code's moving highlight while its work is still running. */
function ShimmerLabel({ text, live }: { text: string; live: boolean }) {
  return (
    <span className="thinking-label">
      <span>{text}</span>
      {live ? <span className="activity-shimmer" aria-hidden="true"><span>{text}</span></span> : null}
    </span>
  );
}

export function LiveActivityRow({ icon, label, live }: { icon: React.ReactNode; label: string; live: boolean }) {
  return (
    <div className={`live-activity-row${live ? " is-live" : ""}`} title={label}>
      <span className="thinking-icon">{icon}</span>
      <ShimmerLabel text={label} live={live} />
    </div>
  );
}

/** A reasoning block. Collapsed, its header follows the newest line so you can watch it think. */
export function LiveThought({ text, live }: { text: string; live: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const body = bodyRef.current;
    if (expanded && live && body) body.scrollTop = body.scrollHeight;
  }, [expanded, live, text]);
  const label = live ? "Thinking" : "Thought";
  const header = expanded ? label : thoughtPreview(text) || label;
  return (
    <div className="thinking-block">
      <button
        type="button"
        className="thinking-disclosure"
        aria-expanded={expanded}
        aria-label={expanded ? label : `${label}: ${header}`}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="thinking-icon"><BrainIcon /></span>
        <ShimmerLabel text={header} live={live} />
        <span className={`thinking-chevron${expanded ? " is-open" : ""}`} aria-hidden="true">›</span>
      </button>
      {expanded ? (
        <div ref={bodyRef} className="thinking-body">
          <ThinkingStreamBody text={text} finished={!live} />
        </div>
      ) : null}
    </div>
  );
}
