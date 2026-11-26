/**
 * Wraps each word of the block a streaming reply is writing in a span, so a
 * new word fades in when it is added instead of popping, as Streamdown's
 * `animated` option does. A word keeps its place as more text follows it, so
 * React keeps its element and the fade plays once.
 *
 * Only the innermost block being written is wrapped: the last paragraph, or
 * the last item of a list. Everything before it is plain text again, which
 * keeps a long reply's element count where it was, and means text already
 * read is never faded a second time when markdown reshapes what holds it (a
 * list whose items become paragraphs once a blank line separates them). Code
 * keeps its spacing and tables arrive whole, so neither is touched.
 */

/** The parts of a hast tree this reads; react-markdown's own types are wider. */
interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

export const STREAM_WORD_CLASS = "chat-stream-word";

const UNTOUCHED = new Set(["pre", "code", "table", "svg", "math"]);
/** Leading whitespace, then each word with the whitespace that follows it. */
const PIECES = /^\s+|\S+\s*/g;

function wrapWords(parent: HastNode): void {
  const children: HastNode[] = [];
  for (const child of parent.children ?? []) {
    if (child.type === "element") {
      if (!UNTOUCHED.has(child.tagName ?? "")) wrapWords(child);
      children.push(child);
      continue;
    }
    if (child.type !== "text" || !child.value?.trim()) {
      children.push(child);
      continue;
    }
    for (const piece of child.value.match(PIECES) ?? []) {
      children.push(
        piece.trim()
          ? {
              type: "element",
              tagName: "span",
              properties: { className: [STREAM_WORD_CLASS] },
              children: [{ type: "text", value: piece }],
            }
          : { type: "text", value: piece },
      );
    }
  }
  parent.children = children;
}

/** Blocks that hold other blocks; the one being written is the last inside them. */
const CONTAINERS = new Set(["ul", "ol", "li", "blockquote"]);
const BLOCKS = new Set(["p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "table", "hr"]);

function lastElement(parent: HastNode): HastNode | undefined {
  const children = parent.children ?? [];
  for (let index = children.length - 1; index >= 0; index--) {
    if (children[index].type === "element") return children[index];
  }
  return undefined;
}

export function rehypeStreamWords() {
  return (tree: HastNode): void => {
    let block = lastElement(tree);
    // Down to the line being written: a list's last item, an item's or a quote's last paragraph.
    while (block && CONTAINERS.has(block.tagName ?? "")) {
      const inner = lastElement(block);
      if (!inner || !BLOCKS.has(inner.tagName ?? "")) break;
      block = inner;
    }
    if (block && !UNTOUCHED.has(block.tagName ?? "")) wrapWords(block);
  };
}
