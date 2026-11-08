import { useVirtualizer, useWindowVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { loadPrivateImage } from "../../api/privateImages";
import { useWorkspaceRuntime } from "../../app/runtime";
import { displayCoverUrl } from "./coverSrc";

/** Mount just the visible threads; the inventory's full array still drives filters and bulk selection. */
export function VirtualListingList<T extends { id: string; cover_photo_url?: string | null; vendoo_cover_url?: string | null }>({ rows, renderRow, children }: {
  rows: readonly T[];
  renderRow: (row: T) => ReactNode;
  children?: ReactNode;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const [mobile, setMobile] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches);
  const [scrollMargin, setScrollMargin] = useState(0);
  const getScrollElement = useCallback(() => listRef.current, []);
  // oxlint-disable-next-line react/incompatible-library -- The app does not use the React Compiler; nothing memoizes this component.
  const elementVirtualizer = useVirtualizer({
    enabled: !mobile,
    count: rows.length,
    getScrollElement,
    getItemKey: index => rows[index].id,
    estimateSize: () => mobile ? 97 : 89,
    overscan: 5,
    scrollMargin,
    initialRect: { width: 300, height: 800 },
  });
  // Safari paints document scrolling behind its floating browser controls.
  // oxlint-disable-next-line react/incompatible-library -- No React Compiler.
  const windowVirtualizer = useWindowVirtualizer({
    enabled: mobile,
    count: rows.length,
    getItemKey: index => rows[index].id,
    estimateSize: () => 97,
    overscan: 5,
    scrollMargin,
    initialRect: { width: 390, height: 800 },
  });
  const virtualizer = mobile ? windowVirtualizer : elementVirtualizer;
  const { hosted } = useWorkspaceRuntime();
  const first = virtualizer.getVirtualItems()[0]?.index ?? 0;
  useEffect(() => {
    // Warm upcoming covers independently of row mounting. Keep downloads small
    // and bounded so they leave capacity for visible images and normal API reads.
    let stopped = false;
    const controller = new AbortController();
    const sources = [...new Set(rows.slice(Math.max(0, first - 10), first + 80)
      .map(row => displayCoverUrl(row.cover_photo_url || row.vendoo_cover_url))
      .filter((src): src is string => Boolean(src)))];
    let next = 0;
    const warm = async () => {
      while (!stopped && next < sources.length) {
        const src = sources[next++];
        try {
          if (hosted && src.startsWith("/api/")) await loadPrivateImage(src, controller.signal);
          else await new Promise<void>(resolve => {
            const image = new Image();
            const stop = () => { image.src = ""; resolve(); };
            controller.signal.addEventListener("abort", stop, { once: true });
            image.onload = image.onerror = () => {
              controller.signal.removeEventListener("abort", stop);
              resolve();
            };
            image.src = src;
          });
        } catch { /* A failed warm-up must not interrupt inventory navigation. */ }
      }
    };
    void Promise.all(Array.from({ length: 3 }, warm));
    return () => { stopped = true; controller.abort(); };
  }, [rows, first, hosted]);

  useLayoutEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const resize = () => setMobile(media.matches);
    media.addEventListener("change", resize);
    const scroll = getScrollElement();
    const list = listRef.current;
    const measure = () => setScrollMargin(mobile && list
      ? list.getBoundingClientRect().top + window.scrollY : 0);
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
