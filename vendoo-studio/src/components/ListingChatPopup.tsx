import { useEffect, useRef, useState, type ReactNode } from "react";
import { MessageSquareIcon, MinusIcon } from "lucide-react";
import { isMacPlatform } from "./chat/ModelPickerContent";
import { Button } from "./ui/button";
import { Kbd } from "./ui/kbd";
import { Spinner } from "./ui/spinner";
import { cn } from "../lib/utils";

/** What the listing's chat reports up, so the minimized pill can show it. */
export interface ListingChatStatus {
  /** The listing this status is for; a switch to another listing reports afresh. */
  convId: string;
  busy: boolean;
  /** What the chat is doing right now, e.g. "Analyzing photos…". Empty when idle. */
  label: string;
  messageCount: number;
  /** No messages yet: the chat is where the seller starts generating. */
  empty: boolean;
}

/** ⌘J on a Mac, Ctrl+J elsewhere. */
export function isChatToggleShortcut(event: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">, mac = isMacPlatform()) {
  const mod = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
  return mod && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "j";
}

/**
 * The listing's chat as a pop-up over the form's corner, like Intercom or
 * Gmail's compose window, so the form keeps the whole workspace. The chat stays
 * mounted while minimized, so a stream, a draft message and the scroll survive.
 */
export function ListingChatPopup({
  convId,
  open,
  onOpenChange,
  status,
  children,
}: {
  convId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: ListingChatStatus | null;
  children: ReactNode;
}) {
  const shortcut = isMacPlatform() ? "⌘J" : "Ctrl+J";
  const current = status?.convId === convId ? status : null;
  // Messages that landed while minimized get a dot on the pill. While open,
  // or on a listing not yet counted, everything on screen counts as seen.
  const [seen, setSeen] = useState<{ convId: string; count: number } | null>(null);
  if (current && (open || seen?.convId !== current.convId) && (seen?.convId !== current.convId || seen.count !== current.messageCount)) {
    setSeen({ convId: current.convId, count: current.messageCount });
  }
  const unread = Boolean(!open && current && seen?.convId === convId && current.messageCount > seen.count);

  // An empty chat holds the Generate button, so a fresh listing opens it.
  const openedEmpty = useRef<string | null>(null);
  useEffect(() => {
    if (!current?.empty || openedEmpty.current === convId) return;
    openedEmpty.current = convId;
    onOpenChange(true);
  }, [convId, current?.empty, onOpenChange]);
  const busy = Boolean(current?.busy);
  const label = current?.label ?? "";

  return (
    <div className="listing-chat" data-open={open || undefined}>
      <section
        id="listing-chat"
        aria-label="Listing chat"
        hidden={!open}
        className="listing-chat-window flex min-h-0 flex-col overflow-hidden rounded-2xl border bg-background shadow-xl"
      >
        <header className="flex shrink-0 items-center gap-2 border-b py-1.5 ps-3 pe-1.5">
          <span className="font-medium text-foreground text-sm">Chat</span>
          {busy && label ? (
            <span className="min-w-0 truncate text-muted-foreground text-xs">{label}</span>
          ) : null}
          <Button
            type="button"
            variant="ghost-muted"
            size="icon-xs"
            className="ms-auto"
            aria-label="Minimize chat"
            title={`Minimize chat (${shortcut})`}
            onClick={() => onOpenChange(false)}
          >
            <MinusIcon />
          </Button>
        </header>
        {children}
      </section>
      {open ? null : (
        <button
          type="button"
          className={cn(
            "listing-chat-pill inline-flex max-w-80 items-center gap-2 rounded-full bg-foreground py-2 ps-3 pe-2 font-medium text-background text-sm shadow-lg",
            "hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          )}
          aria-controls="listing-chat"
          aria-expanded={false}
          title={`Open chat (${shortcut})`}
          onClick={() => onOpenChange(true)}
        >
          {busy ? <Spinner size="md" className="shrink-0" /> : <MessageSquareIcon className="size-4 shrink-0" />}
          <span className="min-w-0 truncate">{busy ? label || "Working…" : "Chat"}</span>
          {unread ? <span aria-label="New reply" className="size-2 shrink-0 rounded-full bg-primary" /> : null}
          <Kbd className="bg-background/15 text-background/70">{shortcut}</Kbd>
        </button>
      )}
    </div>
  );
}
