/**
 * Code blocks and tables in chat markdown, with T3 Code's frames: a code
 * block header naming the language with a copy button, and tables that
 * scroll sideways behind edge fades with a copy-as-Markdown action.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.): MarkdownCodeBlock,
 * MarkdownCodeBlockFrame and MarkdownTable in components/ChatMarkdown.tsx,
 * serializeTable in markdown-clipboard.ts.
 */
import {
  Children,
  isValidElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Tooltip, TooltipPopup, TooltipTrigger } from "./ui/tooltip";

function useCopied(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  const copy = useCallback((text: string) => {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    void navigator.clipboard.writeText(text).then(() => {
      if (timer.current) clearTimeout(timer.current);
      setCopied(true);
      timer.current = setTimeout(() => setCopied(false), 1200);
    }).catch(() => undefined);
  }, []);
  return [copied, copy];
}

function CopyAction({ label, onCopy, copied }: { label: string; onCopy: () => void; copied: boolean }) {
  const text = copied ? "Copied" : label;
  return (
    <Tooltip>
      <TooltipTrigger
        render={<Button type="button" variant="ghost-muted" size="icon-xs" onClick={onCopy} aria-label={text} />}
      >
        {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
      </TooltipTrigger>
      <TooltipPopup side="top">{text}</TooltipPopup>
    </Tooltip>
  );
}

function nodeText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return "";
}

/** The fenced block inside a `pre`: its language and source, or null for anything else. */
export function extractCodeBlock(children: ReactNode): { language: string; code: string } | null {
  const nodes = Children.toArray(children);
  if (nodes.length !== 1) return null;
  const only = nodes[0];
  if (!isValidElement<{ className?: string; children?: ReactNode; node?: { tagName?: string } }>(only)) return null;
  if (only.type !== "code" && only.props.node?.tagName !== "code") return null;
  const language = /language-([\w+#.-]+)/.exec(only.props.className ?? "")?.[1] ?? "text";
  return { language, code: nodeText(only.props.children) };
}

export function MarkdownCodeBlock({ children }: { children: ReactNode }) {
  const [copied, copy] = useCopied();
  const block = extractCodeBlock(children);
  if (!block) return <pre>{children}</pre>;
  return (
    <div
      className="chat-markdown-codeblock my-[0.65rem] overflow-hidden rounded-lg border border-border/70 bg-secondary leading-snug dark:border-transparent dark:bg-input/32"
      data-language={block.language}
      data-wrap="false"
    >
      <div className="chat-markdown-codeblock-header flex items-center justify-between gap-2 pt-1.5 pr-1.5 pb-0 pl-3 select-none">
        <span className="inline-flex min-w-0 items-center gap-1.5 font-mono text-2xs">
          <span className="truncate">{block.language}</span>
        </span>
        <CopyAction label="Copy code" copied={copied} onCopy={() => copy(block.code.replace(/\n$/, ""))} />
      </div>
      <pre>{children}</pre>
    </div>
  );
}

function cellMarkdown(cell: Element): string {
  return (cell.textContent ?? "").replace(/\s+/g, " ").trim().replace(/\|/g, "\\|");
}

/** A rendered table back to GitHub-flavored Markdown, column alignment included. */
export function serializeTableElementToMarkdown(table: Element): string {
  const rows = [...table.querySelectorAll(":scope > thead > tr, :scope > tbody > tr, :scope > tr")];
  const lines: string[] = [];
  for (const row of rows) {
    const cells = [...row.children].filter((cell) => cell.tagName === "TH" || cell.tagName === "TD");
    if (!cells.length) continue;
    lines.push(`| ${cells.map(cellMarkdown).join(" | ")} |`);
    if (lines.length === 1) {
      lines.push(`| ${cells.map((cell) => {
        const align = (cell as HTMLElement).style?.textAlign || cell.getAttribute("align") || "";
        return align === "center" ? ":---:" : align === "right" ? "---:" : align === "left" ? ":---" : "---";
      }).join(" | ")} |`);
    }
  }
  return lines.join("\n");
}

export function MarkdownTable({ children }: { children: ReactNode }) {
  const [copied, copy] = useCopied();
  const scrollerRef = useRef<HTMLDivElement>(null);
  const tableRef = useRef<HTMLTableElement>(null);
  const [edges, setEdges] = useState({ start: false, end: false });

  const measure = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const start = el.scrollLeft > 1;
    const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setEdges((current) => (current.start === start && current.end === end ? current : { start, end }));
  }, []);

  useLayoutEffect(() => {
    measure();
    const el = scrollerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    if (tableRef.current) observer.observe(tableRef.current);
    return () => observer.disconnect();
  }, [measure]);

  return (
    <div className="chat-markdown-table-container" data-expanded="false">
      <div
        ref={scrollerRef}
        onScroll={measure}
        className={cn(
          "w-full max-w-full overflow-x-auto [scrollbar-width:thin] [--fade-size:1.5rem]",
          edges.start && edges.end
            ? "[mask-image:linear-gradient(to_right,transparent,black_var(--fade-size),black_calc(100%-var(--fade-size)),transparent)]"
            : edges.start
              ? "[mask-image:linear-gradient(to_right,transparent,black_var(--fade-size))]"
              : edges.end
                ? "[mask-image:linear-gradient(to_left,transparent,black_var(--fade-size))]"
                : null,
        )}
      >
        <table ref={tableRef}>{children}</table>
      </div>
      <div className="mt-0.5 flex items-center justify-end select-none">
        <CopyAction
          label="Copy as Markdown"
          copied={copied}
          onCopy={() => {
            if (tableRef.current) copy(serializeTableElementToMarkdown(tableRef.current));
          }}
        />
      </div>
    </div>
  );
}
