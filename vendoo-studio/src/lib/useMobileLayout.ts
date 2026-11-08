import { useEffect, useState } from "react";

/** Below this width Anteroom uses its phone layout. */
export const MOBILE_LAYOUT_QUERY = "(max-width: 900px)";

function matches(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia(MOBILE_LAYOUT_QUERY).matches;
}

/** True while the window is phone-sized; follows resizes. */
export function useMobileLayout(): boolean {
  const [isMobile, setIsMobile] = useState(matches);
  useEffect(() => {
    const media = window.matchMedia(MOBILE_LAYOUT_QUERY);
    const onChange = () => setIsMobile(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return isMobile;
}
