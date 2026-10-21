/**
 * Transcript rows with T3 Code's markup and classes: the user bubble, the
 * assistant message with its hover meta, the "Worked for" turn fold and the
 * live "Working for" row.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.):
 * components/chat/MessagesTimeline.tsx (UserTimelineRow, AssistantTimelineRow,
 * AssistantMessageMeta, TurnFoldTimelineRow, WorkingTimelineRow,
 * CollapsibleUserMessageBody) and components/chat/MessageCopyButton.tsx.
 */
import { memo, useEffect, useRef, useState, type ReactNode } from "react";
import { CheckIcon, ChevronDownIcon, ChevronRightIcon, CopyIcon } from "lucide-react";

import { cn } from "../../lib/utils";
import { ChatMarkdown } from "../ChatMarkdown";
import { WorkingDuration } from "../WorkingDuration";
import { Button } from "../ui/button";
import { Dialog, DialogPopup } from "../ui/dialog";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";

/** One image the seller attached to a message. */
export interface MessageAttachment {
  id: string;
  name: string;
  mime_type?: string;
  width?: number | null;
  height?: number | null;
}

export function attachmentUrl(id: string): string {
  return `/api/chat-attachments/${encodeURIComponent(id)}`;
}

// ── Timestamps ──────────────────────────────────────────────────────────

function parseTimestamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  // The server writes naive UTC timestamps for listing chat messages.
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "3:42 PM" today, "Oct 3, 3:42 PM" before, like T3's formatDayAwareTimestamp. */
export function formatDayAwareTimestamp(value: string | null | undefined, now = new Date()): string {
  const date = parseTimestamp(value);
  if (!date) return "";
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (date.toDateString() === now.toDateString()) return time;
  const day = date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${day}, ${time}`;
}

function formatTimestampTooltip(value: string | null | undefined): string {
  const date = parseTimestamp(value);
  return date ? date.toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" }) : "";
}

function MessageTimestamp({ createdAt }: { createdAt: string | null | undefined }) {
  const text = formatDayAwareTimestamp(createdAt);
  if (!text) return null;
  return (
    <Tooltip>
      <TooltipTrigger render={<p className="text-muted-foreground text-xs tabular-nums" />}>{text}</TooltipTrigger>
      <TooltipPopup>{formatTimestampTooltip(createdAt)}</TooltipPopup>
    </Tooltip>
  );
}

// ── Copy ────────────────────────────────────────────────────────────────

export const COPIED_RESET_MS = 1200;

/** Copies `text`; the icon turns into a check for a moment. */
export const MessageCopyButton = memo(function MessageCopyButton({
  text,
  label = "Copy message",
  className,
}: {
  text: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  const copy = () => {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    void navigator.clipboard.writeText(text).then(() => {
      if (timer.current) clearTimeout(timer.current);
      setCopied(true);
      timer.current = setTimeout(() => setCopied(false), COPIED_RESET_MS);
    }).catch(() => undefined);
  };
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            aria-label={copied ? "Copied" : label}
            disabled={copied}
            onClick={copy}
            type="button"
            size="xs"
            variant="ghost-muted"
            className={className}
          />
        }
      >
        {copied ? <CheckIcon className="size-3 text-primary" /> : <CopyIcon className="size-3" />}
      </TooltipTrigger>
      <TooltipPopup>
        <p>{copied ? "Copied" : label}</p>
      </TooltipPopup>
    </Tooltip>
  );
});

// ── User message ────────────────────────────────────────────────────────

const MAX_COLLAPSED_USER_MESSAGE_LINES = 8;
const MAX_COLLAPSED_USER_MESSAGE_LENGTH = 600;
const COLLAPSED_USER_MESSAGE_FADE_HEIGHT_REM = 3;
const COLLAPSED_USER_MESSAGE_FADE_MASK = `linear-gradient(to bottom, black calc(100% - ${COLLAPSED_USER_MESSAGE_FADE_HEIGHT_REM}rem), transparent)`;

export function shouldCollapseUserMessage(text: string): boolean {
  const visible = text.replace(/!?\[([^\]\n]*)\]\((?:<[^>\n]*>|[^\s)]*)\)/g, "$1");
  if (!visible.trim()) return false;
  return visible.length > MAX_COLLAPSED_USER_MESSAGE_LENGTH
    || visible.split("\n").length > MAX_COLLAPSED_USER_MESSAGE_LINES;
}

function CollapsibleUserMessageBody({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const canCollapse = shouldCollapseUserMessage(text);
  const collapsed = canCollapse && !expanded;
  return (
    <div>
      <div
        className={cn("relative", collapsed && "max-h-44 overflow-hidden")}
        data-user-message-collapsed={collapsed ? "true" : "false"}
        style={collapsed ? { WebkitMaskImage: COLLAPSED_USER_MESSAGE_FADE_MASK, maskImage: COLLAPSED_USER_MESSAGE_FADE_MASK } : undefined}
      >
        <ChatMarkdown text={text} lineBreaks />
      </div>
      {canCollapse ? (
        <div className="mt-1.5 flex items-center justify-end gap-2">
          <Button
            type="button"
            size="xs"
            variant="ghost-muted"
            aria-expanded={expanded}
            data-scroll-anchor-ignore
            onClick={() => setExpanded((value) => !value)}
            className="-ml-1 me-auto"
          >
            {expanded ? "Show less" : "Show full message"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function UserImages({ attachments }: { attachments: readonly MessageAttachment[] }) {
  const [open, setOpen] = useState<MessageAttachment | null>(null);
  return (
    <>
      <div className="mb-2 grid max-w-[210px] grid-cols-2 gap-2">
        {attachments.map((image) => (
          <div key={image.id} className="aspect-[4/3] overflow-hidden rounded-lg border border-border/80 bg-background/70">
            <button
              type="button"
              className="block h-full w-full cursor-zoom-in"
              aria-label={`Preview ${image.name}`}
              onClick={() => setOpen(image)}
            >
              <img src={attachmentUrl(image.id)} alt={image.name} decoding="async" className="block size-full object-cover" />
            </button>
          </div>
        ))}
      </div>
      <Dialog open={open !== null} onOpenChange={(next) => { if (!next) setOpen(null); }}>
        <DialogPopup className="max-w-[min(90vw,64rem)] overflow-hidden p-0">
          {open ? (
            <img src={attachmentUrl(open.id)} alt={open.name} className="block max-h-[85vh] w-full object-contain" />
          ) : null}
        </DialogPopup>
      </Dialog>
    </>
  );
}

export function UserMessageRow({
  text,
  createdAt,
  attachments,
  queued = false,
  footer,
}: {
  text: string;
  createdAt?: string | null;
  attachments?: readonly MessageAttachment[] | null;
  /** Held until the running turn finishes: dashed, not sent yet. */
  queued?: boolean;
  footer?: ReactNode;
}) {
  const images = attachments ?? [];
  return (
    <div className="group flex flex-col items-end gap-1" data-message-role="user">
      <div
        className={cn(
          "relative max-w-[80%] rounded-2xl p-3 text-message-foreground",
          queued ? "border border-dashed border-border bg-transparent" : "bg-message",
        )}
      >
        <h3 className="sr-only select-none">You</h3>
        {images.length > 0 ? <UserImages attachments={images} /> : null}
        {text.trim() ? <CollapsibleUserMessageBody text={text} /> : null}
        {footer}
      </div>
      {!queued ? (
        <div className="flex w-full max-w-[80%] items-center justify-end pe-1 text-xs tabular-nums opacity-0 transition-opacity duration-200 pointer-coarse:opacity-100 focus-within:opacity-100 group-hover:opacity-100">
          <div className="flex shrink-0 items-center gap-2">
            <MessageTimestamp createdAt={createdAt} />
            <div className="flex items-center gap-0.5">
              {text.trim() ? <MessageCopyButton text={text} /> : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ── Assistant message ───────────────────────────────────────────────────

export function AssistantMessageRow({
  text,
  createdAt,
  streaming = false,
  messageId,
  children,
}: {
  text: string;
  createdAt?: string | null;
  streaming?: boolean;
  /** Marks the prose as a citation source for "Cite selection". */
  messageId?: string;
  children?: ReactNode;
}) {
  return (
    <div className="group/assistant" data-message-role="assistant">
      <div className="relative min-w-0 px-1 py-0.5">
        <h3 className="sr-only select-none">Studio</h3>
        <div data-citation-source={messageId}>
          <ChatMarkdown text={text} isStreaming={streaming} />
        </div>
        {children}
        {!streaming ? (
          <div className="mt-1.5 flex items-center gap-2 text-xs tabular-nums opacity-0 transition-opacity duration-200 pointer-coarse:opacity-100 focus-within:opacity-100 group-hover/assistant:opacity-100">
            <MessageCopyButton text={text} />
            <MessageTimestamp createdAt={createdAt} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ── Turn fold and working row ───────────────────────────────────────────

export function TurnFoldRow({
  label,
  expanded,
  onToggle,
  endedAt,
}: {
  label: string;
  expanded: boolean;
  onToggle: () => void;
  endedAt?: string | null;
}) {
  const Chevron = expanded ? ChevronDownIcon : ChevronRightIcon;
  return (
    <div className="group/timeline-row relative flex items-center gap-1 border-b border-border/60 pb-2 pe-0.5 pt-1">
      <button
        type="button"
        aria-expanded={expanded}
        data-scroll-anchor-ignore
        onClick={onToggle}
        className="flex cursor-pointer select-none items-center gap-1 rounded-md px-1 text-sm leading-relaxed text-muted-foreground tabular-nums transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/70"
      >
        <span>{label}</span>
        <Chevron className="size-3.5" />
      </button>
      {endedAt ? (
        <span className="pointer-events-none absolute me-1 ms-auto shrink-0 whitespace-nowrap rounded-md text-muted-foreground text-xs tabular-nums opacity-0 group-hover/timeline-row:pointer-events-auto group-hover/timeline-row:static group-hover/timeline-row:opacity-100 group-focus-within/timeline-row:pointer-events-auto group-focus-within/timeline-row:static group-focus-within/timeline-row:opacity-100">
          {formatDayAwareTimestamp(endedAt)}
        </span>
      ) : null}
    </div>
  );
}

export function WorkingRow({ startedAtMs, stopping = false }: { startedAtMs: number | null; stopping?: boolean }) {
  const label = startedAtMs != null ? (
    <>
      {stopping ? "Stopping — " : "Working for "}
      <WorkingDuration startedAtMs={startedAtMs} className="tabular-nums" />
    </>
  ) : stopping ? "Stopping…" : "Working...";
  return (
    <div className="border-b border-border/60 pb-2 pt-1" role="status" aria-live="polite">
      <div className="flex h-6 min-w-0 items-baseline gap-2 px-1 text-sm leading-relaxed text-muted-foreground tabular-nums">
        <span className="relative shrink-0 overflow-hidden whitespace-nowrap">{label}</span>
      </div>
    </div>
  );
}
