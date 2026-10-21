import React, { memo, use, useMemo } from "react";
import ReactMarkdown, { defaultUrlTransform, type ExtraProps } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { cn } from "../lib/utils";
import { CitationChip } from "./CitationChip";
import { MarkdownCodeBlock, MarkdownTable } from "./MarkdownBlocks";
import { ChatCitationRevealContext } from "./chatCitationContext";
import { parseChatCitationHref } from "./chatCitations";
import {
  createIncrementalMarkdownPlugin,
  shouldParseIncrementally,
} from "./markdownIncremental";

interface Props {
  text: string;
  lineBreaks?: boolean;
  isStreaming?: boolean;
  className?: string;
}

type MarkdownProps<Tag extends keyof React.JSX.IntrinsicElements> =
  React.ComponentPropsWithoutRef<Tag> & ExtraProps;

const BASE_PLUGINS = [remarkGfm];
const BASE_PLUGINS_WITH_BREAKS = [remarkGfm, remarkBreaks];

// react-markdown drops unknown protocols; citation links have to survive it.
function markdownUrlTransform(href: string): string | null | undefined {
  return parseChatCitationHref(href) ? href : defaultUrlTransform(href);
}

function MarkdownLink({ href, children }: MarkdownProps<"a">) {
  const reveal = use(ChatCitationRevealContext);
  const citation = href ? parseChatCitationHref(href) : null;
  if (citation) {
    return <CitationChip citation={citation} onReveal={(cited) => reveal?.(cited)} />;
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

/**
 * One map for every render. react-markdown uses these as element types, so a
 * map built inline made each re-render a new type and remounted every table,
 * code block and link in the transcript: on WebKit that pulled the reader's
 * scroll position while a reply streamed.
 */
const COMPONENTS = {
  a: MarkdownLink,
  table({ children }: MarkdownProps<"table">) {
    return <MarkdownTable>{children}</MarkdownTable>;
  },
  pre({ children }: MarkdownProps<"pre">) {
    return <MarkdownCodeBlock>{children}</MarkdownCodeBlock>;
  },
  input({ type, node: _node, ...props }: MarkdownProps<"input">) {
    return <input type={type} disabled={type === "checkbox"} {...props} />;
  },
};

function MarkdownRenderer({ text, lineBreaks = false, isStreaming = false, className }: Props) {
  const incrementalParsing = isStreaming && shouldParseIncrementally(text);
  const remarkPlugins = useMemo(
    () => [
      ...(lineBreaks ? BASE_PLUGINS_WITH_BREAKS : BASE_PLUGINS),
      ...(incrementalParsing ? [createIncrementalMarkdownPlugin()] : []),
    ],
    [incrementalParsing, lineBreaks],
  );

  return (
    <div
      className={cn("chat-markdown w-full min-w-0 [overflow-wrap:anywhere] [word-break:break-word]", className)}
      // Gates the fade-in for blocks that arrive while the response streams.
      data-streaming={isStreaming ? "" : undefined}
    >
      <ReactMarkdown remarkPlugins={remarkPlugins} urlTransform={markdownUrlTransform} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

// A saved answer re-renders with the page on every streamed chunk; its markdown only when its text changes.
export default memo(MarkdownRenderer);
