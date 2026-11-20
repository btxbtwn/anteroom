import { useEffect, useRef } from "react";

/** Warm a destination after a brief hover, or immediately on focus or touch.
 * If `prepare` returns a cleanup function, it runs when hover/focus ends before click. */
export function useNavigationIntent(prepare: () => (() => void) | void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stop = useRef<(() => void) | null>(null);
  const pressed = useRef(false);

  const clearTimer = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  };

  const abort = () => {
    clearTimer();
    const fn = stop.current;
    stop.current = null;
    fn?.();
  };

  const run = () => {
    clearTimer();
    if (stop.current) return;
    const cleanup = prepare();
    if (typeof cleanup === "function") stop.current = cleanup;
  };

  useEffect(() => () => {
    clearTimer();
    stop.current?.();
    stop.current = null;
  }, []);

  return {
    onPointerEnter: () => {
      pressed.current = false;
      clearTimer();
      timer.current = setTimeout(run, 80);
    },
    onPointerLeave: () => {
      clearTimer();
      if (!pressed.current) abort();
      pressed.current = false;
    },
    onPointerDown: () => {
      pressed.current = true;
      run();
    },
    onFocus: run,
    onBlur: () => {
      if (!pressed.current) abort();
    },
  };
}
