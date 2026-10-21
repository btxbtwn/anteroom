import React, { useCallback, useEffect, useRef, useState } from "react";
import { type ChatCitation } from "../chatCitations";
import { revealChatCitation } from "../chatCitationDom";

const NEAR_BOTTOM_PX = 96;
/** T3 Code waits this long before offering "Scroll to end", so a flick does not flash it. */
const SCROLL_PILL_DELAY_MS = 150;

const isNearBottom = (el: HTMLElement) =>
  el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;

/**
 * Keeps a transcript pinned to the newest turn until the reader scrolls up, and
 * offers a "Scroll to end" pill while they are away. Growth is watched with a
 * ResizeObserver on the content, so streamed text, expanding rows and late
 * markdown all keep a pinned transcript at the bottom without per-feature hooks.
 * Toggling a disclosure (`data-scroll-anchor-ignore`) does not re-pin.
 */
export function useChatScroll(resetKey: string) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [chatViewport, setChatViewport] = useState<HTMLDivElement | null>(null);
  const [content, setContent] = useState<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  // Set while a smooth scroll to the end is under way, so its own scroll events do not unpin.
  const followingRef = useRef(false);
  const ignoreResizeRef = useRef(false);
  const pillTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showScrollToEnd, setShowScrollToEnd] = useState(false);

  const setScrollEl = useCallback((el: HTMLDivElement | null) => {
    scrollRef.current = el;
    setChatViewport(el);
  }, []);

  const hidePill = useCallback(() => {
    if (pillTimer.current) clearTimeout(pillTimer.current);
    pillTimer.current = null;
    setShowScrollToEnd(false);
  }, []);

  const offerPill = useCallback(() => {
    if (pillTimer.current) return;
    pillTimer.current = setTimeout(() => {
      pillTimer.current = null;
      setShowScrollToEnd(true);
    }, SCROLL_PILL_DELAY_MS);
  }, []);

  const scrollToEnd = useCallback((smooth = false) => {
    const el = scrollRef.current;
    stickRef.current = true;
    hidePill();
    if (!el) return;
    followingRef.current = smooth;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, [hidePill]);

  const pinChatToBottom = useCallback(() => scrollToEnd(false), [scrollToEnd]);

  useEffect(() => {
    pinChatToBottom();
  }, [resetKey, pinChatToBottom]);

  useEffect(() => {
    if (!content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (ignoreResizeRef.current) {
        ignoreResizeRef.current = false;
        return;
      }
      const el = scrollRef.current;
      if (el && stickRef.current) el.scrollTo({ top: el.scrollHeight });
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [content]);

  useEffect(() => () => {
    if (pillTimer.current) clearTimeout(pillTimer.current);
  }, []);

  const handleChatScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const near = isNearBottom(el);
    if (near) {
      followingRef.current = false;
      stickRef.current = true;
      hidePill();
      return;
    }
    if (followingRef.current) return;
    stickRef.current = false;
    offerPill();
  }, [hidePill, offerPill]);

  const unpin = useCallback(() => {
    followingRef.current = false;
    stickRef.current = false;
  }, []);

  const handleChatWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    if (e.deltaY < 0) unpin();
  }, [unpin]);

  const touchYRef = useRef<number | null>(null);
  const handleChatTouchStart = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    touchYRef.current = e.touches[0]?.clientY ?? null;
  }, []);
  const handleChatTouchMove = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    const y = e.touches[0]?.clientY;
    if (touchYRef.current == null || y == null) return;
    if (y > touchYRef.current + 2) unpin();
    touchYRef.current = y;
  }, [unpin]);

  const handleChatClickCapture = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest?.("[data-scroll-anchor-ignore]")) ignoreResizeRef.current = true;
  }, []);

  const handleShowCitation = useCallback((citation: ChatCitation) => {
    const el = scrollRef.current;
    if (!el) return;
    unpin();
    revealChatCitation(el, citation);
  }, [unpin]);

  return {
    chatViewport,
    setScrollEl,
    setContentEl: setContent,
    pinChatToBottom,
    scrollToEnd,
    showScrollToEnd,
    scrollHandlers: {
      onScroll: handleChatScroll,
      onWheel: handleChatWheel,
      onTouchStart: handleChatTouchStart,
      onTouchMove: handleChatTouchMove,
      onClickCapture: handleChatClickCapture,
    },
    handleShowCitation,
  };
}
