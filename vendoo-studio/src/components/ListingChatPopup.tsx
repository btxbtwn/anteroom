import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Maximize2Icon, MessageSquareIcon, Minimize2Icon, MinusIcon } from "lucide-react";
import { isMacPlatform } from "./chat/ModelPickerContent";
import { PanelResizeHandle, usePanelCollapsed, usePanelWidth, type PanelWidthLimits } from "./PanelResizeHandle";
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

/** The marketplace rail (160px) plus the narrowest comfortable form (560px). */
export const FORM_ROOM = 160 + 560;
export const CHAT_COLUMN_WIDTH: PanelWidthLimits & { default: number } = { min: 300, max: 560, maxVw: 40, default: 360 };

/**
 * Wide enough for the form and the chat side by side, the chat is its own
 * column. Narrower, it floats over the form's corner so the form keeps the width.
 */
export function chatLayout(areaWidth: number, chatWidth: number | null): "column" | "float" {
  return areaWidth > 0 && areaWidth >= FORM_ROOM + (chatWidth ?? CHAT_COLUMN_WIDTH.default) ? "column" : "float";
}

/**
 * The listing's chat. With room it is a column beside the form; without it, a
 * pop-up over the form's corner, like Intercom or
 * Gmail's compose window, that the seller can enlarge or minimize. The chat stays
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

  const rootRef = useRef<HTMLDivElement>(null);
  const areaRef = useRef<HTMLElement | null>(null);
  const [width, setWidth] = usePanelWidth("listing-chat");
  const [expanded, setExpanded] = usePanelCollapsed("listing-chat-expanded");
  const [areaWidth, setAreaWidth] = useState(0);
  // The chat sits inside the form area, so the area's width decides its layout.
  useLayoutEffect(() => {
    const area = rootRef.current?.parentElement;
    if (!area) return;
    areaRef.current = area;
    setAreaWidth(area.getBoundingClientRect().width);
    const observer = new ResizeObserver(([entry]) => setAreaWidth(entry.contentRect.width));
    observer.observe(area);
    return () => observer.disconnect();
  }, []);
  const layout = chatLayout(areaWidth, width);
  const columnWidth = width ?? CHAT_COLUMN_WIDTH.default;

  return (
    <div ref={rootRef} className="listing-chat" data-open={open || undefined} data-layout={layout}
      data-expanded={(layout === "float" && expanded) || undefined}
      style={{ "--listing-chat-width": `${columnWidth}px` } as CSSProperties}>
      {open && layout === "column" ? (
        <PanelResizeHandle
          label="Resize chat"
          panel="after"
          width={width}
          onResize={setWidth}
          absorberRef={areaRef}
          absorberMin={FORM_ROOM + columnWidth}
          min={CHAT_COLUMN_WIDTH.min}
          max={CHAT_COLUMN_WIDTH.max}
          maxVw={CHAT_COLUMN_WIDTH.maxVw}
        />
      ) : null}
      <section
        id="listing-chat"
        aria-label="Listing chat"
        hidden={!open}
        className={cn("listing-chat-window flex min-h-0 flex-col overflow-hidden bg-background",
          layout === "float" && "rounded-2xl border shadow-xl")}
      >
        <header className="flex shrink-0 items-center gap-2 border-b py-1.5 ps-3 pe-1.5">
          <span className="font-medium text-foreground text-sm">Chat</span>
          {busy && label ? (
            <span className="min-w-0 truncate text-muted-foreground text-xs">{label}</span>
          ) : null}
          {layout === "float" ? (
            <Button
              type="button"
              variant="ghost-muted"
              size="icon-xs"
              className="ms-auto"
              aria-label={expanded ? "Make chat smaller" : "Make chat larger"}
              title={expanded ? "Make chat smaller" : "Make chat larger"}
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? <Minimize2Icon /> : <Maximize2Icon />}
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost-muted"
            size="icon-xs"
            className={layout === "float" ? undefined : "ms-auto"}
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
