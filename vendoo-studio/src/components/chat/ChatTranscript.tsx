import type { ReactNode } from "react";
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
  children,
}: {
  scroll: ReturnType<typeof useChatScroll>;
  className?: string;
  laneClassName?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("relative flex min-h-0 flex-1 flex-col", className)}>
      <div
        ref={scroll.setScrollEl}
        className="chat-scroll messages-timeline-scroll h-full min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-y-contain [overflow-anchor:none]"
        {...scroll.scrollHandlers}
      >
        <div
          ref={scroll.setContentEl}
          className={cn("chat-content-lane mx-auto flex w-full min-w-0 max-w-(--chat-content-max-width) flex-col px-4 pt-4 pb-2", laneClassName)}
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
