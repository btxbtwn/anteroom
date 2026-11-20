import { useEffect, useRef } from "react";

/** Warm a destination after a brief hover, or immediately on focus or touch. */
export function useNavigationIntent(prepare: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancel = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  };
  const immediate = () => {
    cancel();
    prepare();
  };
  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
  }, []);
  return {
    onPointerEnter: () => {
      cancel();
      timer.current = setTimeout(immediate, 80);
    },
    onPointerLeave: cancel,
    onPointerDown: immediate,
    onFocus: immediate,
    onBlur: cancel,
  };
}
