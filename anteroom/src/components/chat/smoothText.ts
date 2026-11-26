/**
 * Paces a streaming reply. Providers and relays deliver text unevenly: a word
 * at a time, or several lines in one chunk. Showing each chunk as it lands
 * makes the reply jump. This reveals what has arrived at a speed that follows
 * how much is waiting, so the reader sees a steady flow that is never far
 * behind. The Vercel AI SDK's smoothStream does the same on the server, a
 * word every 10ms; doing it here covers every provider and relay.
 */
import { useEffect, useState } from "react";

/** How long the reveal takes to close most of the gap to what has arrived. */
const CATCH_UP_MS = 180;
/** The slowest the reveal moves while text is waiting, in characters a second. */
const MIN_CHARS_PER_SECOND = 60;
/** The least time between two steps: 25 a second, each one a markdown render. */
const STEP_MS = 40;

/** How many characters to show after `elapsedMs`, given `shown` of `total` so far. */
export function revealStep(shown: number, total: number, elapsedMs: number): number {
  if (shown >= total) return total;
  const charsPerMs = Math.max(MIN_CHARS_PER_SECOND / 1000, (total - shown) / CATCH_UP_MS);
  return Math.min(total, shown + Math.max(1, Math.round(charsPerMs * elapsedMs)));
}

function reducedMotion(): boolean {
  return typeof window !== "undefined" && Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

/**
 * The part of a growing `text` to show now. Text already there when the
 * component mounts shows at once, so an answer reopened part way through is
 * not typed out again; what arrives afterwards is revealed over the next
 * moments. With reduced motion everything shows as it arrives.
 */
export function useSmoothText(text: string): string {
  const [shown, setShown] = useState(text.length);
  const total = text.length;
  // A shorter text is a new answer, or one being replayed from its first word.
  if (shown > total) setShown(total);

  useEffect(() => {
    if (shown >= total) return;
    if (reducedMotion()) {
      setShown(total);
      return;
    }
    const startedAt = performance.now();
    let frame = requestAnimationFrame(function step(now: number) {
      const elapsed = now - startedAt;
      if (elapsed < STEP_MS) {
        frame = requestAnimationFrame(step);
        return;
      }
      // A tab left in the background comes back with a long gap, and so with everything.
      setShown((current) => revealStep(current, total, elapsed));
    });
    return () => cancelAnimationFrame(frame);
  }, [shown, total]);

  return shown >= total ? text : text.slice(0, shown);
}
