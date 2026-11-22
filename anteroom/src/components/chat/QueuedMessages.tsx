/**
 * Follow-ups held until the running turn finishes, with T3 Code's queue
 * controls: send one now (cutting off the reply being written), edit it in
 * the composer, or remove it. A long queue shows its first rows and a button
 * for the rest.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.): components/chat/QueuedRunsControl.tsx.
 */
import { useId, useState } from "react";
import { ArrowUpIcon, ChevronDownIcon, PencilIcon, XIcon } from "lucide-react";
import { cn } from "../../lib/utils";
import { Button, InlineButton } from "../ui/button";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";
import { type QueuedChatMessage } from "./chatQueueStore";
import { modifierKeyLabel } from "./ComposerPrimaryActions";
import { UserMessageRow } from "./TimelineRows";

const QUEUE_VISIBLE_ROWS = 3;

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

export function QueuedMessages({
  queue,
  canSendNow,
  onSendNow,
  onEdit,
  onRemove,
  editingId = null,
  onCancelEdit,
  editBlockedReason = null,
  imageCount = () => 0,
}: {
  queue: readonly QueuedChatMessage[];
  /** The reply being written can take a steer, so a queued message can go now. */
  canSendNow: boolean;
  onSendNow?: (id: string) => void;
  onEdit?: (id: string) => void;
  onRemove: (id: string) => void;
  editingId?: string | null;
  onCancelEdit?: () => void;
  /** Why editing is unavailable right now, such as text already in the box. */
  editBlockedReason?: string | null;
  imageCount?: (message: QueuedChatMessage) => number;
}) {
  const [showAll, setShowAll] = useState(false);
  const listId = useId();
  if (queue.length === 0) return null;
  const modifier = modifierKeyLabel();
  const collapsible = queue.length > QUEUE_VISIBLE_ROWS;
  const visible = collapsible && !showAll ? queue.slice(0, QUEUE_VISIBLE_ROWS - 1) : queue;
  return (
    <section aria-label={plural(queue.length, "queued message", "queued messages")} className="relative flex flex-col gap-2 pb-4" data-chat-queue>
      <span className="sr-only" aria-live="polite">{`${queue.length} queued`}</span>
      <ol id={listId} className="flex flex-col gap-3">
        {visible.map((queued, position) => {
          const images = imageCount(queued);
          const editing = queued.id === editingId;
          const first = position === 0;
          const status = queued.steer ? "Sending next" : editing ? "Editing in the message box" : "Queued";
          return (
            <li
              key={queued.id}
              data-queued-message-id={queued.id}
              aria-label={`Queued message ${position + 1} of ${queue.length}, not sent`}
              aria-current={editing ? "true" : undefined}
            >
              <UserMessageRow
                queued
                text={queued.text || (images ? "" : "Empty message")}
                footer={
                  <div className="mt-1.5 flex flex-wrap items-center gap-1">
                    <span
                      className={cn("me-auto px-1 text-xs", editing ? "text-foreground" : "text-muted-foreground")}
                      title={first ? "Sends when the current run finishes" : "Sends after the messages above it"}
                    >
                      {status}
                      {images ? ` · ${plural(images, "image", "images")}` : ""}
                    </span>
                    {editing ? (
                      <Button type="button" size="xs" variant="ghost" onClick={onCancelEdit}>
                        Cancel
                      </Button>
                    ) : (
                      <>
                        {canSendNow && onSendNow && !queued.steer ? (
                          <Tooltip>
                            <TooltipTrigger render={<span className="flex shrink-0" />}>
                              <Button type="button" size="xs" variant="ghost-muted" onClick={() => onSendNow(queued.id)}>
                                <ArrowUpIcon />
                                Send now
                              </Button>
                            </TooltipTrigger>
                            <TooltipPopup>
                              {first
                                ? `Cut off this reply and send this message (${modifier}+Shift+Enter)`
                                : "Cut off this reply and send this message"}
                            </TooltipPopup>
                          </Tooltip>
                        ) : null}
                        {onEdit ? (
                          <Tooltip>
                            <TooltipTrigger render={<span className="flex shrink-0" />}>
                              <Button
                                type="button"
                                size="icon-xs"
                                variant="ghost-muted"
                                aria-label="Edit queued message"
                                disabled={editBlockedReason !== null || images > 0 || queued.steer}
                                onClick={() => onEdit(queued.id)}
                              >
                                <PencilIcon />
                              </Button>
                            </TooltipTrigger>
                            <TooltipPopup>
                              {images > 0
                                ? "Messages with images can't be edited. Remove it and send it again."
                                : editBlockedReason
                                  ?? (position === queue.length - 1 ? "Edit in the message box (Alt+↑)" : "Edit in the message box")}
                            </TooltipPopup>
                          </Tooltip>
                        ) : null}
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <Button
                                type="button"
                                size="icon-xs"
                                variant="ghost-muted"
                                aria-label="Remove queued message"
                                onClick={() => onRemove(queued.id)}
                              />
                            }
                          >
                            <XIcon />
                          </TooltipTrigger>
                          <TooltipPopup>Remove from queue</TooltipPopup>
                        </Tooltip>
                      </>
                    )}
                  </div>
                }
              />
            </li>
          );
        })}
      </ol>
      {collapsible ? (
        <div className="self-end pe-1 text-xs">
          <InlineButton
            tone="muted"
            aria-expanded={showAll}
            aria-controls={listId}
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => setShowAll((value) => !value)}
          >
            {showAll ? "Show fewer" : `Show all ${queue.length} queued`}
            <ChevronDownIcon
              aria-hidden
              className={cn("size-3.5 transition-transform duration-150 motion-reduce:transition-none", showAll && "rotate-180")}
            />
          </InlineButton>
        </div>
      ) : null}
    </section>
  );
}
