/**
 * Wraps each word of the block a streaming reply is writing in a span, so a
 * new word fades in when it is added instead of popping, as Streamdown's
 * `animated` option does. A word keeps its place as more text follows it, so
 * React keeps its element and the fade plays once.
 *
 * Only the last block is wrapped: finished blocks are plain text again, which
 * keeps a long reply's element count where it was. Code keeps its spacing and
 * tables arrive whole, so neither is touched.
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

export function rehypeStreamWords() {
  return (tree: HastNode): void => {
    const blocks = tree.children ?? [];
    for (let index = blocks.length - 1; index >= 0; index--) {
      const block = blocks[index];
      if (block.type !== "element") continue;
      if (!UNTOUCHED.has(block.tagName ?? "")) wrapWords(block);
      return;
    }
  };
}
