import { useEffect, useState, type ReactNode } from "react";
import { ChevronDownIcon } from "lucide-react";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import type { useChatScroll } from "./useChatScroll";

/**
 * The scrolling transcript shared by the listing chat and the assistant, with
 * T3 Code's "Scroll to end" pill centered above the composer.
 * Classes follow T3 Code's MessagesTimeline and ChatView (MIT, © 2026 T3 Tools Inc.).
 */
export function ChatTranscript({
  scroll,
  className,
  laneClassName,
  busy = false,
  doneMessage = "Reply finished.",
  children,
}: {
  scroll: ReturnType<typeof useChatScroll>;
  className?: string;
  laneClassName?: string;
  /** True while a reply or generation is running. */
  busy?: boolean;
  /** Said to screen readers once when the running reply ends. */
  doneMessage?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("relative flex min-h-0 flex-1 flex-col", className)}>
      {/* One polite region for the whole transcript. Streamed text is never
          announced word by word; only that the reply has finished. */}
      <DoneAnnouncer busy={busy} doneMessage={doneMessage} />
      <div
        ref={scroll.setScrollEl}
        className="chat-scroll messages-timeline-scroll h-full min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-y-contain [overflow-anchor:none]"
        {...scroll.scrollHandlers}
      >
        <div
          ref={scroll.setContentEl}
          className={cn("chat-content-lane @container/chat-lane mx-auto flex w-full min-w-0 max-w-(--chat-content-max-width) flex-col px-4 pt-4 pb-2", laneClassName)}
        >
          {children}
        </div>
      </div>
      {scroll.showScrollToEnd ? (
        <div className="chat-scroll-to-bottom pointer-events-none absolute inset-x-0 bottom-1 z-30 flex justify-center py-1.5">
          <Button
            aria-label="Scroll to end"
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => scroll.scrollToEnd(true)}
            className="pointer-events-auto"
            size="xs"
            variant="glass"
          >
            <ChevronDownIcon className="size-3.5" />
            Scroll to end
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** The text for the transcript's status region: set once when a run ends, then
 * cleared so the next run's end is announced again. */
function DoneAnnouncer({ busy, doneMessage }: { busy: boolean; doneMessage: string }) {
  const [wasBusy, setWasBusy] = useState(busy);
  const [message, setMessage] = useState(doneMessage);
  const [text, setText] = useState("");
  // What the run was doing is read while it runs: once it ends, the caller's
  // label has already moved on.
  if (busy && message !== doneMessage) setMessage(doneMessage);
  if (busy !== wasBusy) {
    setWasBusy(busy);
    setText(busy ? "" : message);
  }
  useEffect(() => {
    if (!text) return;
    const timer = setTimeout(() => setText(""), 4000);
    return () => clearTimeout(timer);
  }, [text]);
  return <div role="status" className="sr-only">{text}</div>;
}
