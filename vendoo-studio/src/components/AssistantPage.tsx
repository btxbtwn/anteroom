import { Fragment, useEffect, useRef, useState, type MouseEvent } from "react";
import { ArrowUpRightIcon, CircleAlertIcon, HourglassIcon, PercentIcon, TagIcon, TrendingUpIcon, type LucideIcon } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { ASSISTANT_MEMORY_KEY, AssistantRefused, type AssistantMessage, type AssistantThread } from "../api/assistant";
import type { ChatAttachment, ChatAttachmentUpload } from "../api/types";
import { invalidateTopics } from "../cloud/liveUpdates";
import { addToast } from "../ui/toast";
import { useMobileLayout } from "../lib/useMobileLayout";
import { AssistantAutomationCard } from "./AssistantAutomationCard";
import { AssistantChangesCard } from "./AssistantChangesCard";
import { type ComposerPromptEditorHandle } from "./ComposerPromptEditor";
import { newQueuedChatMessage, type QueuedChatMessage } from "./chatMessageQueue";
import { coalesceSseEvents, emptySseParts, type SseParts } from "./sseCoalesce";
import { streamingDisplayText } from "./streamingText";
import { settleAgents } from "./liveTrace";
import { useComposerImages } from "./chat/ComposerAttachments";
import { ChatComposer } from "./chat/ChatComposer";
import { chatTime } from "./chat/chatHistory";
import { foldSseEvent } from "./chat/chatStream";
import { ChatTranscript } from "./chat/ChatTranscript";
import { FoldedTurnRow, LiveWork } from "./chat/LiveWork";
import { QueuedMessages } from "./chat/QueuedMessages";
import { AssistantMessageRow, UserMessageRow } from "./chat/TimelineRows";
import { useChatScroll } from "./chat/useChatScroll";
import { placeTurnFolds, turnHasWork, type FoldedTurn } from "./chat/workLogModel";
import { Alert, AlertAction, AlertTitle } from "./ui/alert";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";

/** Questions that show what the assistant can see, offered before the first one is asked. */
export const ASSISTANT_STARTERS: readonly { text: string; icon: LucideIcon }[] = [
  { text: "How did last month compare with the month before?", icon: TrendingUpIcon },
  { text: "Which brands and categories make me the most profit?", icon: TagIcon },
  { text: "What has been listed longest, and what should I do with it?", icon: HourglassIcon },
  { text: "Mark down everything listed over 90 days by 15%.", icon: PercentIcon },
];

/** The home screen's greeting, by the seller's local time. */
export function greeting(now = new Date()): string {
  const hour = now.getHours();
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 18) return "Good afternoon";
  return "Good evening";
}

/** How many past chats the home screen offers to pick back up, as Gemini's "Recent". */
export const RECENT_CHATS = 3;

/** A mouse or trackpad: put the cursor in the box. On a phone the keyboard would cover the questions. */
function finePointer(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(pointer: fine)").matches;
}

const THREADS_KEY = ["assistant", "threads"];

function messagesKey(threadId: string) {
  return ["assistant", "messages", threadId] as const;
}
/** How often, and how many times, a dropped connection is retried before saying so. */
const RECONNECT_MS = 1500;
const RECONNECT_TRIES = 8;
const LOST_CONNECTION =
  "Lost the connection to Anteroom. If it was still answering, the answer appears here when you come back.";
/** How an answer points at a listing: `[title](#listing-<id>)`, or `#<id>` in answers saved before the server fixed that form. */
const LISTING_LINK = /^#(?:listing-([\w-]+)|([0-9a-f]{12}))$/;

/** The listing an answer's link opens, or null for any other link. */
export function linkedListingId(href: string | null | undefined): string | null {
  const match = LISTING_LINK.exec(href ?? "");
  return match?.[1] ?? match?.[2] ?? null;
}

/**
 * What of the turn being written still has to be drawn. The saved conversation
 * can refresh mid-answer and bring the question, or the finished answer, with
 * it; drawing the live copy as well would show it twice.
 */
export function liveParts(live: Pick<LiveTurn, "question" | "asked">, turns: AssistantMessage[]): { question: boolean; answer: boolean } {
  if (live.question === null) return { question: false, answer: turns[turns.length - 1]?.role !== "assistant" };
  return { question: turns.length <= live.asked, answer: turns.length < live.asked + 2 };
}

interface Props {
  onOpenProviders: () => void;
  onOpenListing: (id: string) => void;
  /** The open conversation. Null on a home screen that has not started one yet. */
  threadId?: string | null;
  /** A question started a conversation, or the seller picked a recent one; open it. */
  onThreadReady?: (id: string) => void;
  /** Open the full chat history. */
  onShowHistory?: () => void;
}

/** A follow-up asked while an answer is still being written, with the images sent along. */
export interface QueuedQuestion extends QueuedChatMessage {
  uploads: ChatAttachmentUpload[];
  shown: ChatAttachment[];
  /** The conversation it was asked in; null on a home screen that has not started one. */
  threadId: string | null;
}

/**
 * What asking does right now: send it, or hold it until the answer being
 * written ends. Asking while busy used to do nothing at all, and the
 * follow-up sat in the composer as if Studio had hung.
 */
export function askNow(busy: boolean, queued: readonly QueuedQuestion[]): "send" | "queue" {
  return busy || queued.length ? "queue" : "send";
}

export interface LiveTurn {
  /** Null when an answer already under way was picked back up; its question is among the saved turns. */
  question: string | null;
  /** Images sent with the question, shown on the live copy until it is saved. */
  questionImages: ChatAttachment[];
  /** How many turns were saved when the question was asked. */
  asked: number;
  /** The stream so far: the answer text, the work log and any subagents. */
  parts: SseParts;
  startedAtMs: number;
}

/** Finished turns of this session keep their "Worked for" rows while the app is open, per conversation. */
const sessionFolds = new Map<string, FoldedTurn[]>();
let foldSeq = 0;

function foldsFor(threadId: string): FoldedTurn[] {
  return sessionFolds.get(threadId) ?? [];
}

/**
 * A chat about the business as a whole, apart from any listing's chat. Every
 * question is answered from all of Studio's data. An answer can propose
 * changes to listings; they are written only when the seller applies them.
 */
export function AssistantPage({ onOpenProviders, onOpenListing, threadId = null, onThreadReady, onShowHistory }: Props) {
  const queryClient = useQueryClient();
  const messages = useQuery({
    queryKey: messagesKey(threadId ?? ""),
    queryFn: ({ signal }) => api.assistant.messages(threadId!, { signal }),
    enabled: Boolean(threadId),
  });
  const threads = useQuery({
    queryKey: THREADS_KEY,
    queryFn: ({ signal }) => api.assistant.threads({ signal }),
  });
  const [input, setInput] = useState("");
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [folds, setFolds] = useState<FoldedTurn[]>(() => (threadId ? foldsFor(threadId) : []));
  const [error, setError] = useState("");
  const [stopping, setStopping] = useState(false);
  /** True while this page follows an answer, including a reattach that has not heard anything yet. */
  const [following, setFollowing] = useState(false);
  const [queued, setQueued] = useState<QueuedQuestion[]>([]);
  const controller = useRef<AbortController | null>(null);
  const composerRef = useRef<ComposerPromptEditorHandle | null>(null);
  const images = useComposerImages();
  const scroll = useChatScroll("assistant");
  const phone = useMobileLayout();

  const turns = messages.data ?? [];
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  const threadIdRef = useRef(threadId);
  threadIdRef.current = threadId;
  /** The conversation the in-flight follow belongs to, so selecting a thread this page just created does not cancel the question. */
  const followThread = useRef<string | null>(null);

  const keepFold = (id: string, fold: FoldedTurn) => {
    const next = [...foldsFor(id), fold];
    sessionFolds.set(id, next);
    if (threadIdRef.current === id) setFolds(next);
  };

  /**
   * Follow an answer to its end: a new one when there is a question, a new
   * answer to the thread's unanswered question when retrying, otherwise
   * whichever is already being written. Studio keeps writing when this
   * connection drops, so a drop is retried, not reported.
   */
  const follow = async (
    question: string | null,
    uploads: ChatAttachmentUpload[] = [],
    shownImages: ChatAttachment[] = [],
    inThread: string | null = threadIdRef.current,
    retry = false,
  ) => {
    if (controller.current) return;
    let id = inThread;
    if (question !== null && !id) {
      try {
        const created = await api.assistant.createThread();
        id = created.id;
        followThread.current = id;
        onThreadReady?.(id);
        // The sidebar lists it, and the page selects it, while the answer is still being written.
        void queryClient.invalidateQueries({ queryKey: THREADS_KEY });
      } catch (failure) {
        setError(failure instanceof Error ? failure.message : "Could not start a conversation.");
        setInput((current) => current || question);
        return;
      }
    }
    if (!id) return;
    followThread.current = id;
    const threadKey = messagesKey(id);
    const abort = new AbortController();
    controller.current = abort;
    let asking = question !== null;
    let retrying = retry;
    setFollowing(true);
    const asked = turnsRef.current.length;
    const baseIds = turnsRef.current.map((turn) => turn.id);
    const startedAtMs = Date.now();
    let dropped = false;
    let parts = emptySseParts();
    let stopped = false;
    const show = () =>
      setLive({ question, questionImages: shownImages, asked, startedAtMs, parts: { ...parts } });
    if (question !== null || retry) {
      scroll.pinChatToBottom();
      setError("");
      if (question !== null) setInput("");
      parts.status = "Reading your shop…";
      show();
    }
    try {
      for (let tries = 0; ; tries += 1) {
        // A resumed answer is replayed from its first word.
        parts = emptySseParts();
        // Paint at most every 50ms; every event still folds into `parts` first.
        const paint = coalesceSseEvents(() => show());
        const handlers = {
          onEvent: (event: string, data: string) => {
            foldSseEvent(parts, event, data);
            paint.push(event, parts);
          },
        };
        try {
          const result = asking && question !== null
            ? await api.assistant.ask(question, handlers, abort.signal, uploads, id)
            : retrying
              ? await api.assistant.retry(handlers, abort.signal, id)
              : await api.assistant.resume(handlers, abort.signal, id);
          paint.flush();
          if (result.error) setError(result.error);
          break;
        } catch (failure) {
          paint.flush();
          if (abort.signal.aborted) {
            stopped = true;
            break;
          }
          if (failure instanceof AssistantRefused) {
            setError(failure.message);
            if (asking && question !== null) setInput((current) => current || question);
            break;
          }
          asking = false;
          retrying = false;
          dropped = true;
          if (tries >= RECONNECT_TRIES) {
            setError(LOST_CONNECTION);
            break;
          }
          if (question !== null) {
            parts.status = "Reconnecting…";
            show();
          }
          await new Promise((resolve) => setTimeout(resolve, RECONNECT_MS));
          if (abort.signal.aborted) {
            stopped = true;
            break;
          }
        }
      }
    } finally {
      const endedAtMs = Date.now();
      if (turnHasWork(parts.trace)) {
        foldSeq += 1;
        keepFold(id, {
          id: `assistant-turn-${foldSeq}`,
          trace: parts.trace,
          agents: settleAgents(parts.agents, endedAtMs),
          startedAtMs,
          endedAtMs,
          cancelled: stopped,
          baseIds,
        });
      }
      // Show the saved turns before dropping the live one, so the answer does not blink.
      void queryClient.invalidateQueries({ queryKey: THREADS_KEY });
      void queryClient.invalidateQueries({ queryKey: ASSISTANT_MEMORY_KEY });
      await queryClient.invalidateQueries({ queryKey: threadKey });
      if (question !== null && dropped && !abort.signal.aborted) {
        // The connection may have dropped before Studio ever received the question.
        const saved = queryClient.getQueryData<AssistantMessage[]>(threadKey);
        if (saved && !saved.some((turn) => turn.role === "user" && turn.text === question)) {
          setError("Could not reach Anteroom. Check the connection and ask again.");
          setInput((current) => current || question);
        }
      }
      // A newer follow may own the page by now; leave its turn alone.
      if (controller.current === abort) {
        controller.current = null;
        setLive(null);
        setFollowing(false);
      }
    }
  };
  const followRef = useRef(follow);
  useEffect(() => {
    followRef.current = follow;
  });

  // Pick an answer back up after opening a conversation, leaving the page, or locking the phone.
  useEffect(() => {
    if (!threadId) return;
    const reattach = () => {
      if (document.hidden || controller.current) return;
      setError((current) => (current === LOST_CONNECTION ? "" : current));
      void followRef.current(null);
    };
    // A question that just created this thread is already being followed.
    if (!(followThread.current === threadId && controller.current)) {
      setLive(null);
      setError("");
      setFolds(foldsFor(threadId));
      reattach();
    }
    document.addEventListener("visibilitychange", reattach);
    return () => {
      document.removeEventListener("visibilitychange", reattach);
      // Render already moved the ref when the seller switched threads. A strict-mode
      // re-run keeps the same id, and must not cancel the question; nor does the
      // page selecting the thread its own question just created.
      if (threadIdRef.current !== threadId && followThread.current !== threadIdRef.current) {
        controller.current?.abort();
        controller.current = null;
        followThread.current = null;
        setFollowing(false);
      }
    };
  }, [threadId]);

  // Follow-ups wait for the conversation they were asked in; another thread drops them.
  useEffect(() => {
    setQueued((current) => {
      const kept = current.filter((message) => message.threadId === threadId || message.threadId === followThread.current);
      return kept.length === current.length ? current : kept;
    });
  }, [threadId]);

  // Send the oldest held follow-up once the answer before it has ended.
  useEffect(() => {
    if (live || controller.current || !queued.length) return;
    const [next, ...rest] = queued;
    setQueued(rest);
    void followRef.current(next.text, next.uploads, next.shown, next.threadId ?? followThread.current ?? threadIdRef.current);
  }, [live, queued]);

  const ask = async (raw: string) => {
    const question = raw.trim();
    const attached = images.images;
    if (!question && !attached.length) return;
    const uploads = attached.length ? await images.prepare(attached) : [];
    if (uploads === null) return;
    // The live bubble shows the images by name until the saved turn brings them back.
    const shown = attached.map((image, index) => ({ id: "", name: uploads[index]?.name ?? image.file.name, mime_type: image.file.type }));
    images.clear();
    if (askNow(Boolean(live || controller.current), queued) === "queue") {
      setInput("");
      scroll.pinChatToBottom();
      const asked = followThread.current ?? threadIdRef.current;
      setQueued((current) => [...current, { ...newQueuedChatMessage(question), uploads, shown, threadId: asked }]);
      return;
    }
    void follow(question, uploads, shown);
  };

  const stop = async () => {
    // The server saves the partial answer before it answers; say so meanwhile.
    setStopping(true);
    try {
      await api.assistant.stop().catch(() => undefined);
      controller.current?.abort();
    } finally {
      setStopping(false);
    }
  };

  // Applied changes reach every page that shows a listing, a total or a box.
  const changesSettled = (message: AssistantMessage, applied: boolean) => {
    queryClient.setQueryData<AssistantMessage[]>(messagesKey(threadId ?? ""), (saved) =>
      saved?.map((turn) => (turn.id === message.id ? message : turn)));
    if (applied) void invalidateTopics(queryClient, ["listings"]);
  };

  const automationSettled = (message: AssistantMessage) => {
    queryClient.setQueryData<AssistantMessage[]>(messagesKey(threadId ?? ""), (saved) =>
      saved?.map((turn) => (turn.id === message.id ? message : turn)));
    void queryClient.invalidateQueries({ queryKey: ["automations"] });
  };

  // Answers link the listings they name; open those in Studio instead of a new tab.
  const openLinkedListing = (event: MouseEvent<HTMLDivElement>) => {
    const link = (event.target as HTMLElement).closest("a");
    const id = linkedListingId(link?.getAttribute("href"));
    if (!id) return;
    event.preventDefault();
    const known = queryClient.getQueryData<{ id: string }[]>(["conversations"]);
    if (known && !known.some((listing) => listing.id === id)) {
      addToast({ type: "warning", title: "That listing is no longer in Anteroom." });
      return;
    }
    onOpenListing(id);
  };

  const drawn = live ? liveParts(live, turns) : { question: false, answer: false };
  const visible = live && drawn.answer ? streamingDisplayText(live.parts.content) : "";
  const empty = !live && !turns.length && !messages.isLoading && !messages.isError;
  const needsProvider = /sign in with chatgpt/i.test(error);
  // An answer that failed, or that a restart cut off, left the last question unanswered.
  const unanswered = !live && !following && !messages.isFetching && turns[turns.length - 1]?.role === "user";
  const retry = () => {
    setError("");
    void follow(null, [], [], threadIdRef.current, true);
  };
  const foldPositions = placeTurnFolds(turns, folds);
  const foldsAt = (index: number) => folds.filter((_, k) => foldPositions[k] === index);
  const liveTail = live && drawn.answer && !live.parts.trace.some((entry) => entry.kind === "phase")
    ? live.parts.status || "Answering…"
    : "";

  const composer = (
    <div className="mx-auto w-full max-w-208">
      <ChatComposer
        composerRef={composerRef}
        input={input}
        onInputChange={setInput}
        placeholder="Ask about sales, inventory or listings…"
        autoFocus={empty && finePointer()}
        images={images}
        busy={Boolean(live)}
        stopping={stopping}
        onSend={() => { void ask(input); }}
        onStop={() => { void stop(); }}
        stopLabel="Stop answering"
        queueLabel="Send when this answer is finished"
      />
    </div>
  );

  // The server lists chats newest first. A phone keeps its home to the greeting
  // and questions; its header's History button holds the chats.
  const recent: AssistantThread[] = phone ? [] : (threads.data ?? []).slice(0, RECENT_CHATS);

  if (empty) {
    return (
      <div className="assistant-page flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto px-4">
          {/* Centered a little above the middle, as wide as the composer's surface so the questions line up with it. */}
          <div className="my-auto flex w-full max-w-[calc(var(--chat-content-max-width)+2rem)] flex-col pt-8 pb-[12vh] max-[900px]:pb-6">
            <h1 className="mx-3 mb-5 text-center font-semibold sm:mx-4 text-2xl tracking-tight text-foreground">{greeting()}</h1>
            {/* On a phone the box stays at the bottom, within thumb reach, as in the conversation. */}
            {phone ? null : composer}
            <ul aria-label="Example questions" className="mx-3 mt-2 list-none divide-y sm:mx-4 divide-border/60 p-0">
              {ASSISTANT_STARTERS.map(({ text, icon: Icon }) => (
                <li key={text}>
                  <button
                    type="button"
                    className="group flex min-h-10 w-full cursor-pointer items-center gap-3 py-2 text-left text-muted-foreground text-sm transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none max-[900px]:min-h-11"
                    onClick={() => { void ask(text); }}
                  >
                    <Icon aria-hidden className="size-4 shrink-0" />
                    <span className="flex-1">{text}</span>
                    <ArrowUpRightIcon aria-hidden className="size-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 max-[900px]:hidden" />
                  </button>
                </li>
              ))}
            </ul>
            {recent.length ? (
              <section aria-labelledby="assistant-recent" className="mx-3 mt-6 sm:mx-4">
                <div className="flex items-center justify-between">
                  <h2 id="assistant-recent" className="font-medium text-muted-foreground text-xs">Recent chats</h2>
                  {onShowHistory && (threads.data?.length ?? 0) > RECENT_CHATS ? (
                    <Button type="button" size="xs" variant="ghost-muted" className="-me-2" onClick={onShowHistory}>
                      Show all
                    </Button>
                  ) : null}
                </div>
                <ul aria-label="Recent chats" className="mt-1 list-none divide-y divide-border/60 p-0">
                  {recent.map((thread) => (
                    <li key={thread.id}>
                      <button
                        type="button"
                        className="flex min-h-10 w-full cursor-pointer items-center gap-3 py-2 text-left text-sm text-foreground transition-colors hover:text-foreground/80 focus-visible:outline-none focus-visible:underline max-[900px]:min-h-11"
                        onClick={() => onThreadReady?.(thread.id)}
                      >
                        <span className="min-w-0 flex-1 truncate">{thread.title}</span>
                        <span className="shrink-0 text-muted-foreground text-xs tabular-nums">{chatTime(thread)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        </div>
        {phone ? composer : null}
      </div>
    );
  }

  return (
    <div className="assistant-page flex min-h-0 min-w-0 flex-1 flex-col">
      <ChatTranscript scroll={scroll} busy={Boolean(live)} doneMessage="Answer finished." laneClassName="max-w-208 px-4 pt-8 sm:px-8">
        <div className="contents" onClick={openLinkedListing}>
          {messages.isLoading && !turns.length ? (
            <div className="flex justify-center py-4"><Spinner size="md" tone="muted" /></div>
          ) : null}
          {messages.isError ? (
            <p className="text-muted-foreground text-sm">{(messages.error as Error).message || "Could not load the conversation."}</p>
          ) : null}
          {turns.map((turn, index) => (
            <Fragment key={turn.id}>
              {foldsAt(index).map((fold) => <FoldedTurnRow key={fold.id} turn={fold} />)}
              <div className="pb-4">
                {turn.role === "user" ? (
                  <UserMessageRow text={turn.text} createdAt={turn.created_at} attachments={turn.attachments} />
                ) : (
                  <AssistantMessageRow text={turn.text} createdAt={turn.created_at}>
                    {turn.changes ? (
                      <AssistantChangesCard message={turn} changes={turn.changes} onSettled={changesSettled} />
                    ) : null}
                    {turn.automation ? (
                      <AssistantAutomationCard message={turn} automation={turn.automation} onSettled={automationSettled} />
                    ) : null}
                  </AssistantMessageRow>
                )}
              </div>
            </Fragment>
          ))}
          {foldsAt(turns.length).map((fold) => <FoldedTurnRow key={fold.id} turn={fold} />)}
          {live ? (
            <>
              {drawn.question && live.question !== null ? (
                <div className="pb-4">
                  <UserMessageRow
                    text={live.question}
                    footer={live.questionImages.length ? (
                      <p className="mt-1 text-muted-foreground text-xs">
                        {live.questionImages.map((image) => image.name).join(", ")}
                      </p>
                    ) : null}
                  />
                </div>
              ) : null}
              {drawn.answer ? (
                <LiveWork
                  busy
                  stopping={stopping}
                  workStartedAtMs={live.startedAtMs}
                  streamTrace={live.parts.trace}
                  streamAgents={live.parts.agents}
                  localLive
                  tailLabel={visible ? "" : liveTail}
                />
              ) : null}
              {visible ? (
                <div className="pb-4">
                  <AssistantMessageRow text={visible} streaming />
                </div>
              ) : null}
            </>
          ) : null}
          <QueuedMessages
            queue={queued}
            onRemove={(id) => setQueued((current) => current.filter((message) => message.id !== id))}
          />
          {error || unanswered ? (
            <Alert variant="error" className="my-2">
              <CircleAlertIcon />
              <AlertTitle className="whitespace-pre-wrap">{error || "This question was not answered."}</AlertTitle>
              {needsProvider ? (
                <AlertAction>
                  <Button type="button" size="xs" variant="outline" onClick={onOpenProviders}>
                    Open provider settings
                  </Button>
                </AlertAction>
              ) : unanswered ? (
                <AlertAction>
                  <Button type="button" size="xs" variant="outline" onClick={retry}>
                    Retry
                  </Button>
                </AlertAction>
              ) : null}
            </Alert>
          ) : null}
        </div>
      </ChatTranscript>
      {composer}
    </div>
  );
}
