import type { BrowserInputEvent } from "../api/client";

/* A phone keyboard only opens for a focused text box, and it reports what it did
   by changing that box: autocorrect and swipe typing rewrite whole words with no
   key events. Studio keeps a hidden box holding KEYBOARD_REST and replays each
   change on the Vendoo draft. The newline stops the keyboard from joining a new
   word onto earlier text and gives Backspace something to delete. */
export const KEYBOARD_REST = "\n";

function isHighSurrogate(char: string | undefined): boolean {
  if (!char) return false;
  const code = char.charCodeAt(0);
  return code >= 0xd800 && code <= 0xdbff;
}

function tap(key: string): BrowserInputEvent[] {
  return [
    { kind: "key", type: "down", key, modifiers: 0 },
    { kind: "key", type: "up", key, modifiers: 0 },
  ];
}

/** The input that turns the hidden box's `previous` text into `next` on the draft. */
export function typedEvents(previous: string, next: string): BrowserInputEvent[] {
  let kept = 0;
  while (kept < previous.length && kept < next.length && previous[kept] === next[kept]) kept += 1;
  if (isHighSurrogate(previous[kept - 1])) kept -= 1;

  const events: BrowserInputEvent[] = [];
  for (let index = Array.from(previous.slice(kept)).length; index > 0; index -= 1) events.push(...tap("Backspace"));
  next.slice(kept).split("\n").forEach((text, index) => {
    if (index) events.push(...tap("Enter"));
    if (text) events.push({ kind: "text", text });
  });
  return events;
}
