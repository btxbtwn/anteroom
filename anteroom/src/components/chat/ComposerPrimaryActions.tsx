/**
 * The composer's send and stop buttons, with T3 Code's markup: a round
 * up-arrow send that grows a little on hover, a red stop with a rounded
 * square while a run is going, and beside it a steer button (click sends now,
 * modifier-click queues) or, when the run cannot take a steer, a queue button.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.): components/chat/ComposerPrimaryActions.tsx.
 */
import { CheckIcon, ListPlusIcon } from "lucide-react";
import { cn } from "../../lib/utils";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";

const SEND_CLASS =
  "relative isolate flex h-9 w-9 items-center justify-center overflow-hidden rounded-full shadow-xs transition-all duration-150 enabled:cursor-pointer enabled:inset-shadow-control-highlight hover:scale-105 active:inset-shadow-control-pressed active:shadow-none disabled:pointer-events-none disabled:opacity-64 disabled:shadow-none disabled:hover:scale-100 sm:h-8 sm:w-8 pointer-coarse:size-11 sm:pointer-coarse:size-11 [&_svg]:pointer-events-none bg-message-action text-message-action-foreground enabled:shadow-message-action/24 hover:bg-message-action-hover motion-reduce:transition-none motion-reduce:hover:scale-100";

const STOP_CLASS =
  "flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full bg-destructive/90 text-white shadow-xs shadow-destructive/24 inset-shadow-control-highlight transition-colors duration-150 hover:bg-destructive active:inset-shadow-control-pressed active:shadow-none disabled:pointer-events-none disabled:opacity-64 sm:h-8 sm:w-8 pointer-coarse:size-11 sm:pointer-coarse:size-11 [&_svg]:pointer-events-none";

/** Keeps focus in the editor when a button is pressed with the pointer. */
const keepFocus = { onPointerDown: (event: { preventDefault: () => void }) => event.preventDefault() };

function SendArrow() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <path d="M7 11.5V2.5M7 2.5L3 6.5M7 2.5L11 6.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** "⌘" on a Mac, "Ctrl" elsewhere, for the queue and steer hints. */
export function modifierKeyLabel(): string {
  const platform = typeof navigator === "undefined" ? "" : navigator.platform || navigator.userAgent;
  return /mac|iphone|ipad/i.test(platform) ? "⌘" : "Ctrl";
}

function QueueIcon() {
  return <ListPlusIcon className="size-4" aria-hidden="true" />;
}

export function ComposerPrimaryActions({
  busy,
  stopping = false,
  canSubmit,
  onSend,
  onStop,
  sendLabel = "Send message",
  stopLabel = "Stop",
  queueLabel = "Queue for when the current run finishes",
  canSteer = false,
  editingQueued = false,
}: {
  busy: boolean;
  stopping?: boolean;
  canSubmit: boolean;
  /** `alternate` is true for a modifier-click: queue instead of steer, or the reverse. */
  onSend: (alternate: boolean) => void;
  onStop?: () => void;
  sendLabel?: string;
  stopLabel?: string;
  /** While busy, a follow-up queues instead; null hides the queue button. */
  queueLabel?: string | null;
  /** The run can take a steer: a click cuts it off and sends, a modifier-click queues. */
  canSteer?: boolean;
  /** The box holds a queued message being edited; the button saves it back. */
  editingQueued?: boolean;
}) {
  const modifier = modifierKeyLabel();
  const send = (event: { metaKey: boolean; ctrlKey: boolean }) => onSend(event.metaKey || event.ctrlKey);
  if (editingQueued) {
    return (
      <div className="flex items-center justify-end gap-2">
        <Tooltip>
          <TooltipTrigger render={<span className="inline-flex" />}>
            <button
              type="button"
              className={SEND_CLASS}
              {...keepFocus}
              onClick={send}
              disabled={!canSubmit}
              aria-label="Save queued message"
            >
              <CheckIcon className="size-4" aria-hidden="true" />
            </button>
          </TooltipTrigger>
          <TooltipPopup>Save queued message</TooltipPopup>
        </Tooltip>
      </div>
    );
  }
  if (busy) {
    const followUp = canSubmit && queueLabel ? (
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              className={SEND_CLASS}
              {...keepFocus}
              onClick={send}
              aria-label={canSteer ? "Steer message" : "Queue message"}
            />
          }
        >
          {canSteer ? <SendArrow /> : <QueueIcon />}
        </TooltipTrigger>
        <TooltipPopup>
          {canSteer
            ? `Click to send now and cut off this reply, ${modifier}-click or ${modifier}+Enter to queue`
            : queueLabel}
        </TooltipPopup>
      </Tooltip>
    ) : null;
    // A touch screen has no modifier key, so it gets queue as a button of its own.
    const touchQueue = canSubmit && queueLabel && canSteer ? (
      <button
        type="button"
        className={cn(SEND_CLASS, "hidden bg-secondary text-foreground hover:bg-accent pointer-coarse:flex")}
        {...keepFocus}
        onClick={() => onSend(true)}
        aria-label="Queue message"
      >
        <QueueIcon />
      </button>
    ) : null;
    return (
      <div className="flex items-center justify-end gap-2">
        {touchQueue}
        {followUp}
        {onStop ? (
          <Tooltip>
            <TooltipTrigger
              render={
                <button
                  type="button"
                  className={cn(STOP_CLASS)}
                  {...keepFocus}
                  disabled={stopping}
                  onClick={onStop}
                  aria-label={stopLabel}
                  aria-keyshortcuts="Escape"
                />
              }
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
                <rect x="2" y="2" width="8" height="8" rx="1.5" />
              </svg>
            </TooltipTrigger>
            <TooltipPopup>{stopping ? "Stopping…" : `${stopLabel ?? "Stop"} (Esc)`}</TooltipPopup>
          </Tooltip>
        ) : null}
      </div>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex" />}>
        <button type="button" className={SEND_CLASS} {...keepFocus} onClick={send} disabled={!canSubmit} aria-label={sendLabel}>
          <SendArrow />
        </button>
      </TooltipTrigger>
      <TooltipPopup>{sendLabel}</TooltipPopup>
    </Tooltip>
  );
}
