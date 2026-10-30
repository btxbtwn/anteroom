import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Mount just the visible threads; the inventory's full array still drives filters and bulk selection. */
export function VirtualListingList<T extends { id: string }>({ rows, renderRow, children }: {
  rows: readonly T[];
  renderRow: (row: T) => ReactNode;
  children?: ReactNode;
}) {
  "use no memo"; // TanStack's mutable virtualizer must be read on every render.
  const listRef = useRef<HTMLDivElement>(null);
  const [mobile, setMobile] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches);
  const [scrollMargin, setScrollMargin] = useState(0);
  const getScrollElement = useCallback(() => mobile ? listRef.current?.closest("aside") ?? null : listRef.current, [mobile]);
  // oxlint-disable-next-line react/incompatible-library -- This component explicitly opts out of compiler memoization above.
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement,
    getItemKey: index => rows[index].id,
    estimateSize: () => mobile ? 76 : 64,
    overscan: 5,
    scrollMargin,
    initialRect: { width: 300, height: 800 },
  });

  useLayoutEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const resize = () => setMobile(media.matches);
    media.addEventListener("change", resize);
    const scroll = getScrollElement();
    const list = listRef.current;
    const measure = () => setScrollMargin(mobile && scroll && list
      ? list.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop : 0);
    measure();
    const observer = new ResizeObserver(measure);
    if (scroll) observer.observe(scroll);
    if (list) observer.observe(list);
    return () => { media.removeEventListener("change", resize); observer.disconnect(); };
  }, [getScrollElement, mobile]);

  return <div ref={listRef}
    className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-(--sidebar-content-inset) pb-2 max-[900px]:flex-none max-[900px]:overflow-visible max-[900px]:pb-[calc(72px+env(safe-area-inset-bottom,0px))]">
    <div role="list" aria-label="Listings" style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
      {virtualizer.getVirtualItems().map(item => <div key={item.key} ref={virtualizer.measureElement} data-index={item.index}
        role="listitem" aria-posinset={item.index + 1} aria-setsize={rows.length}
        style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${item.start - scrollMargin}px)` }}>
        {renderRow(rows[item.index])}
      </div>)}
    </div>
    {children}
  </div>;
}
