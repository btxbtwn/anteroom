import React, { useCallback, useEffect, useRef, useState } from "react";
import type { Message } from "../../api/types";
import { type ChatCitation } from "../chatCitations";
import { revealChatCitation } from "../chatCitationDom";
import { type LiveTraceEntry } from "../liveTrace";

const isNearBottom = (el: HTMLDivElement, threshold = 96) =>
  el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;

/** Keeps the transcript pinned to the newest turn until the reader scrolls up. */
export function useChatScroll(
  convId: string,
  messages: Message[] | undefined,
  streamText: string,
  streamTrace: LiveTraceEntry[],
) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [chatViewport, setChatViewport] = useState<HTMLDivElement | null>(null);
  const setScrollEl = useCallback((el: HTMLDivElement | null) => {
    scrollRef.current = el;
    setChatViewport(el);
  }, []);
  const stickToBottomRef = useRef(true);

  const scrollChatToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo(0, el.scrollHeight);
  }, []);

  const pinChatToBottom = useCallback(() => {
    stickToBottomRef.current = true;
    scrollChatToBottom();
  }, [scrollChatToBottom]);

  useEffect(() => {
    stickToBottomRef.current = true;
    scrollChatToBottom();
  }, [convId, scrollChatToBottom]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickToBottomRef.current) return;
    if (!isNearBottom(el)) {
      stickToBottomRef.current = false;
      return;
    }
    scrollChatToBottom();
  }, [messages, streamText, streamTrace, scrollChatToBottom]);

  const handleChatScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = isNearBottom(el);
  }, []);

  const handleChatWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    if (e.deltaY < 0) stickToBottomRef.current = false;
  }, []);

  const touchYRef = useRef<number | null>(null);
  const handleChatTouchStart = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    touchYRef.current = e.touches[0]?.clientY ?? null;
  }, []);
  const handleChatTouchMove = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    const y = e.touches[0]?.clientY;
    if (touchYRef.current == null || y == null) return;
    if (y > touchYRef.current + 2) stickToBottomRef.current = false;
    touchYRef.current = y;
  }, []);

  const handleShowCitation = useCallback((citation: ChatCitation) => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = false;
    revealChatCitation(el, citation);
  }, []);

  return {
    chatViewport,
    setScrollEl,
    pinChatToBottom,
    handleChatScroll,
    handleChatWheel,
    handleChatTouchStart,
    handleChatTouchMove,
    handleShowCitation,
  };
}
