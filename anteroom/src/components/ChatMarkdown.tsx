import { lazy, Suspense } from "react";
import { cn } from "../lib/utils";

const MarkdownRenderer = lazy(() => import("./MarkdownRenderer"));

/** T3 Code's transcript prose: the size, leading and color of a chat message. */
export const CHAT_PROSE_CLASS = "text-sm leading-relaxed text-foreground/[calc(80%+var(--appearance-contrast-boost)/5)]";

interface Props {
  text: string;
  lineBreaks?: boolean;
  /** Fades in each block that arrives mid-response and parses incrementally. */
  isStreaming?: boolean;
  className?: string;
}

/** Renders chat markdown; shows plain text until the markdown bundle loads. */
export function ChatMarkdown({ text, lineBreaks = false, isStreaming = false, className }: Props) {
  return (
    <Suspense
      fallback={
        <div className={cn("chat-markdown w-full min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]", className)}>{text}</div>
      }
    >
      <MarkdownRenderer text={text} lineBreaks={lineBreaks} isStreaming={isStreaming} className={className} />
    </Suspense>
  );
}
