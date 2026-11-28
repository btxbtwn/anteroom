/**
 * Paces a streaming reply. Providers and relays deliver text unevenly: a word
 * at a time, or several lines in one chunk. Showing each chunk as it lands
 * makes the reply jump. This reveals what has arrived at a speed that follows
 * how much is waiting, so the reader sees a steady flow that is never far
 * behind. The Vercel AI SDK's smoothStream does the same on the server, a
 * word every 10ms; doing it here covers every provider and relay.
 */
import { useEffect, useRef, useState } from "react";

/** How long the reveal takes to close most of the gap to what has arrived. */
const CATCH_UP_MS = 180;
/** The slowest the reveal moves while text is waiting, in characters a second. */
const MIN_CHARS_PER_SECOND = 60;
/** The least time between two steps: 25 a second, each one a markdown render. */
const STEP_MS = 40;
/** Longest slow visible-frame gap treated as one pacing step, so slow paints never dump a whole chunk. */
const MAX_STEP_MS = 60;
/** A gap at least this long means the tab was away, so reveal what arrived while hidden. */
const BACKGROUND_GAP_MS = 1000;

/** How many characters to show after `elapsedMs`, given `shown` of `total` so far. */
export function revealStep(shown: number, total: number, elapsedMs: number): number {
  if (shown >= total) return total;
  const elapsed = Math.max(0, elapsedMs);
  const catchUp = (total - shown) * (1 - Math.exp(-elapsed / CATCH_UP_MS));
  const floor = (MIN_CHARS_PER_SECOND / 1000) * elapsed;
  return Math.min(total, shown + Math.max(1, Math.round(Math.max(floor, catchUp))));
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
  // When the reveal last moved. Kept across renders, so text that arrives
  // more often than the reveal steps never pushes the next step back.
  const lastStepAt = useRef<number | null>(null);
  // A shorter text is a new answer, or one being replayed from its first word.
  // With reduced motion nothing is paced: what arrived is what shows.
  if (shown > total || (shown < total && reducedMotion())) setShown(total);

  useEffect(() => {
    if (shown >= total) {
      // Caught up: a pause before the next chunk is not time spent revealing it.
      lastStepAt.current = null;
      return;
    }
    lastStepAt.current ??= performance.now();
    let frame = requestAnimationFrame(function step(now: number) {
      const elapsed = now - (lastStepAt.current ?? now);
      if (elapsed < STEP_MS) {
        frame = requestAnimationFrame(step);
        return;
      }
      lastStepAt.current = now;
      // A slow visible frame still paces the chunk instead of dumping it at once;
      // a tab left in the background comes back with a long gap, and so with everything.
      const stepMs = elapsed < BACKGROUND_GAP_MS ? Math.min(elapsed, MAX_STEP_MS) : elapsed;
      setShown((current) => revealStep(current, total, stepMs));
    });
    return () => cancelAnimationFrame(frame);
  }, [shown, total]);

  return shown >= total ? text : text.slice(0, shown);
}
