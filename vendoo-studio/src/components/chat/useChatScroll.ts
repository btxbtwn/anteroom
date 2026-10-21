import React, { useCallback, useEffect, useRef, useState } from "react";
import { type ChatCitation } from "../chatCitations";
import { revealChatCitation } from "../chatCitationDom";

const NEAR_BOTTOM_PX = 96;
/** T3 Code waits this long before offering "Scroll to end", so a flick does not flash it. */
const SCROLL_PILL_DELAY_MS = 150;
/** How long a key press counts as the reader scrolling by keyboard. */
const KEY_SCROLL_MS = 800;
const UP_KEYS = new Set(["ArrowUp", "PageUp", "Home"]);

export interface ScrollSample {
  top: number;
  height: number;
  client: number;
}

/**
 * What one scroll event means. Only the reader moving up unpins: a scroll up
 * while they are dragging the scrollbar or pressing a key (wheel and touch
 * unpin on their own events). Growth and layout under a pinned transcript fire
 * scroll events too, and WebKit delivers them late and sometimes with the
 * offset pulled up, so the offset alone never means the reader left.
 */
export function readScroll(
  previous: ScrollSample,
  next: ScrollSample,
  { following, reading }: { following: boolean; reading: boolean },
): "pin" | "unpin" | "keep" {
  if (next.height - next.top - next.client <= NEAR_BOTTOM_PX) return "pin";
  if (following) return "keep";
  if (reading && next.top < previous.top - 1) return "unpin";
  return "keep";
}

const sample = (el: HTMLElement): ScrollSample => ({ top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight });

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
  const lastSample = useRef<ScrollSample>({ top: 0, height: 0, client: 0 });
  // The reader is dragging the scrollbar, or pressed a scrolling key just now.
  const draggingRef = useRef(false);
  const keyScrollAt = useRef(0);
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
    if (smooth) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    else el.scrollTop = el.scrollHeight;
    lastSample.current = sample(el);
  }, [hidePill]);

  const pinChatToBottom = useCallback(() => scrollToEnd(false), [scrollToEnd]);

  useEffect(() => {
    pinChatToBottom();
  }, [resetKey, pinChatToBottom]);

  // The content growing, and the viewport shrinking (a phone's keyboard, the
  // composer growing), both keep a pinned transcript on its newest line.
  useEffect(() => {
    if (!content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (ignoreResizeRef.current) {
        ignoreResizeRef.current = false;
        return;
      }
      const el = scrollRef.current;
      if (!el) return;
      if (stickRef.current && !followingRef.current) el.scrollTop = el.scrollHeight;
      lastSample.current = sample(el);
    });
    observer.observe(content);
    if (chatViewport) observer.observe(chatViewport);
    return () => observer.disconnect();
  }, [content, chatViewport]);

  useEffect(() => () => {
    if (pillTimer.current) clearTimeout(pillTimer.current);
  }, []);

  const handleChatScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const next = sample(el);
    const reading = readScroll(lastSample.current, next, {
      following: followingRef.current,
      reading: draggingRef.current || Date.now() - keyScrollAt.current < KEY_SCROLL_MS,
    });
    lastSample.current = next;
    if (reading === "pin") {
      followingRef.current = false;
      stickRef.current = true;
      hidePill();
      return;
    }
    if (reading === "unpin") stickRef.current = false;
    if (stickRef.current) {
      // Content moved under a pinned transcript; put it back on the newest line.
      if (!followingRef.current) {
        el.scrollTop = el.scrollHeight;
        lastSample.current = sample(el);
      }
      return;
    }
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

  // A press on the scroller itself, not its content, is a press on its scrollbar.
  const handleChatPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    draggingRef.current = true;
    const release = () => {
      draggingRef.current = false;
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
  }, []);

  const handleChatKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (UP_KEYS.has(e.key) || (e.key === " " && e.shiftKey)) keyScrollAt.current = Date.now();
  }, []);

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
      onPointerDown: handleChatPointerDown,
      onKeyDown: handleChatKeyDown,
      onClickCapture: handleChatClickCapture,
    },
    handleShowCitation,
  };
}
