// Matches T3 Code's paragraph-streaming boundary rules: only expose markdown
// that cannot change shape when the next provider delta arrives.
const MARKDOWN_FENCE_PATTERN = /^( *)(`{3,}|~{3,})/;
const BLANK_LINE_PATTERN = /^[ \t]*$/;
const LIST_ITEM_START_PATTERN = /^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]/;

export function stableStreamingText(text: string): string {
  let openFence: { marker: string; indent: number } | null = null;
  let boundary = -1;
  let lineStart = 0;

  for (;;) {
    const newline = text.indexOf("\n", lineStart);
    const line = text
      .slice(lineStart, newline === -1 ? text.length : newline)
      .replace(/[ \t\r]+$/, "");

    if (openFence === null && lineStart > 0 && LIST_ITEM_START_PATTERN.test(line)) {
      boundary = lineStart;
    }
    if (newline === -1) break;

    const fenceMatch = MARKDOWN_FENCE_PATTERN.exec(line);
    if (fenceMatch) {
      const indent = fenceMatch[1]!.length;
      const marker = fenceMatch[2]!;
      if (openFence === null) {
        openFence = { marker, indent };
      } else if (
        marker[0] === openFence.marker[0]
        && marker.length >= openFence.marker.length
        && indent <= openFence.indent + 3
        && line.length === indent + marker.length
      ) {
        openFence = null;
        boundary = newline + 1;
      }
    } else if (openFence === null && BLANK_LINE_PATTERN.test(line) && lineStart > 0) {
      boundary = newline + 1;
    }
    lineStart = newline + 1;
  }

  return boundary === -1 ? "" : text.slice(0, boundary);
}

const TABLE_ROW_PATTERN = /^[ \t]*\|/;
const OPEN_FENCE_PATTERN = /(?:^|\n) {0,3}(`{3,}|~{3,})[^\n]*\n/;

/**
 * What a streaming reply shows: every finished block, plus the block being
 * written up to its last whole word. Inline markup the model has opened but
 * not closed yet is closed here, so a reader never sees `**` turn into bold a
 * moment later (Streamdown's incomplete-markdown approach). Tables wait for
 * their whole block, because a header row only becomes a table once the
 * delimiter row arrives.
 */
export function streamingDisplayText(text: string): string {
  const stable = stableStreamingText(text);
  const tail = text.slice(stable.length);
  if (!tail) return stable;
  // An open code fence renders to the end of the document; show whole lines.
  if (OPEN_FENCE_PATTERN.test(tail)) return stable + tail.slice(0, tail.lastIndexOf("\n") + 1);
  if (TABLE_ROW_PATTERN.test(tail)) return stable;
  return stable + closeInlineMarkdown(wholeWords(tail));
}

function wholeWords(tail: string): string {
  const cut = Math.max(tail.lastIndexOf(" "), tail.lastIndexOf("\n"), tail.lastIndexOf("\t"));
  return cut === -1 ? "" : tail.slice(0, cut).replace(/[ \t]+$/, "");
}

/** Closes inline code, bold, italic and strikethrough left open at the end of a
 * partial block, and holds back a link whose target has not arrived. */
function closeInlineMarkdown(block: string): string {
  let text = block;
  // A link or image is shown once its target closes.
  const link = /!?\[[^\]\n]*(?:\]\([^)\n]*)?$|!?\[[^\]\n]*\]$/.exec(text);
  if (link) text = text.slice(0, link.index).replace(/[ \t]+$/, "");

  const closers: string[] = [];
  let inCode = false;
  const open = new Map<string, boolean>();
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === "\\") {
      i++;
      continue;
    }
    if (char === "`") {
      inCode = !inCode;
      continue;
    }
    if (inCode) continue;
    if (text.startsWith("~~", i)) {
      open.set("~~", !open.get("~~"));
      i++;
      continue;
    }
    if (char !== "*") continue;
    let run = 1;
    while (text[i + run] === "*") run++;
    const lineStart = i === 0 || text[i - 1] === "\n";
    // "* item" is a list marker, not emphasis.
    if (!(run === 1 && lineStart && text[i + 1] === " ")) {
      if (run >= 2) open.set("**", !open.get("**"));
      if (run % 2 === 1) open.set("*", !open.get("*"));
    }
    i += run - 1;
  }
  if (inCode) {
    if (text.endsWith("`")) text = text.slice(0, -1);
    else closers.push("`");
  }
  for (const marker of ["*", "**", "~~"]) {
    if (!open.get(marker)) continue;
    // A marker with nothing after it yet would print literally once closed.
    if (text.endsWith(marker)) text = text.slice(0, -marker.length).replace(/[ \t]+$/, "");
    else closers.push(marker);
  }
  return text + closers.join("");
}
