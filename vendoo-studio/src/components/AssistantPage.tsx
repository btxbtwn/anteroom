import { Fragment, useEffect, useRef, useState, type MouseEvent } from "react";
import { CircleAlertIcon } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { AssistantRefused, type AssistantMessage } from "../api/assistant";
import type { ChatAttachment, ChatAttachmentUpload } from "../api/types";
import { confirmDialog } from "../ui/confirmDialog";
import { addToast } from "../ui/toast";
import { AssistantChangesCard } from "./AssistantChangesCard";
import { type ComposerPromptEditorHandle } from "./ComposerPromptEditor";
import { coalesceSseEvents, emptySseParts, type SseParts } from "./sseCoalesce";
import { stableStreamingText } from "./streamingText";
import { settleAgents } from "./liveTrace";
import { useComposerImages } from "./chat/ComposerAttachments";
import { ChatComposer } from "./chat/ChatComposer";
import { foldSseEvent } from "./chat/chatStream";
import { ChatTranscript } from "./chat/ChatTranscript";
import { FoldedTurnRow, LiveWork } from "./chat/LiveWork";
import { AssistantMessageRow, UserMessageRow } from "./chat/TimelineRows";
import { useChatScroll } from "./chat/useChatScroll";
import { placeTurnFolds, turnHasWork, type FoldedTurn } from "./chat/workLogModel";
import { Alert, AlertAction, AlertTitle } from "./ui/alert";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { Textarea } from "./ui/textarea";

/** Questions that show what the assistant can see, offered before the first one is asked. */
export const ASSISTANT_STARTERS = [
  "How did last month compare with the month before?",
  "Which brands and categories make me the most profit?",
  "What has been listed longest, and what should I do with it?",
  "Which wholesale boxes paid off, and which did not?",
  "Mark down everything listed over 90 days by 15%.",
  "Find listings whose size differs between forms and fix them.",
];

const MESSAGES_KEY = ["assistant", "messages"];
const NOTE_KEY = ["assistant", "note"];
const NOTE_MAX = 4000;
/** How often, and how many times, a dropped connection is retried before saying so. */
const RECONNECT_MS = 1500;
const RECONNECT_TRIES = 8;
const LOST_CONNECTION =
  "Lost the connection to Studio. If it was still answering, the answer appears here when you come back.";
/** How an answer points at a listing: `[title](#listing-<id>)`. */
const LISTING_LINK = /^#listing-([\w-]+)$/;

/** The listing an answer's link opens, or null for any other link. */
export function linkedListingId(href: string | null | undefined): string | null {
  return LISTING_LINK.exec(href ?? "")?.[1] ?? null;
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

/** Finished turns of this session keep their "Worked for" rows while the app is open. */
let sessionFolds: FoldedTurn[] = [];
let foldSeq = 0;

/**
 * A chat about the business as a whole, apart from any listing's chat. Every
 * question is answered from all of Studio's data. An answer can propose
 * changes to listings; they are written only when the seller applies them.
 */
export function AssistantPage({ onOpenProviders, onOpenListing }: Props) {
  const queryClient = useQueryClient();
  const messages = useQuery({ queryKey: MESSAGES_KEY, queryFn: api.assistant.messages });
  const [input, setInput] = useState("");
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [folds, setFolds] = useState<FoldedTurn[]>(sessionFolds);
  const [error, setError] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const composerRef = useRef<ComposerPromptEditorHandle | null>(null);
  const images = useComposerImages();
  const scroll = useChatScroll("assistant");

  const turns = messages.data ?? [];
  const turnsRef = useRef(turns);
  turnsRef.current = turns;

  const keepFold = (fold: FoldedTurn) => {
    sessionFolds = [...sessionFolds, fold];
    setFolds(sessionFolds);
  };

  /**
   * Follow an answer to its end: a new one when there is a question, otherwise
   * whichever is already being written. Studio keeps writing when this
   * connection drops, so a drop is retried, not reported.
   */
  const follow = async (question: string | null, uploads: ChatAttachmentUpload[] = [], shownImages: ChatAttachment[] = []) => {
    if (controller.current) return;
    const abort = new AbortController();
    controller.current = abort;
    let asking = question !== null;
    const asked = turnsRef.current.length;
    const baseIds = turnsRef.current.map((turn) => turn.id);
    const startedAtMs = Date.now();
    let dropped = false;
    let parts = emptySseParts();
    let stopped = false;
    const show = () =>
      setLive({ question, questionImages: shownImages, asked, startedAtMs, parts: { ...parts } });
    if (question !== null) {
      scroll.pinChatToBottom();
      setError("");
      setInput("");
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
            ? await api.assistant.ask(question, handlers, abort.signal, uploads)
            : await api.assistant.resume(handlers, abort.signal);
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
        keepFold({
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
      await queryClient.invalidateQueries({ queryKey: MESSAGES_KEY });
      if (question !== null && dropped && !abort.signal.aborted) {
        // The connection may have dropped before Studio ever received the question.
        const saved = queryClient.getQueryData<AssistantMessage[]>(MESSAGES_KEY);
        if (saved && !saved.some((turn) => turn.role === "user" && turn.text === question)) {
          setError("Could not reach Studio. Check the connection and ask again.");
          setInput((current) => current || question);
        }
      }
      // A newer follow may own the page by now; leave its turn alone.
      if (controller.current === abort) {
        controller.current = null;
        setLive(null);
      }
    }
  };
  const followRef = useRef(follow);
  useEffect(() => {
    followRef.current = follow;
  });

  // Pick an answer back up after leaving the page, locking the phone, or reloading.
  useEffect(() => {
    const reattach = () => {
      if (document.hidden) return;
      setError((current) => (current === LOST_CONNECTION ? "" : current));
      void followRef.current(null);
    };
    reattach();
    document.addEventListener("visibilitychange", reattach);
    return () => {
      document.removeEventListener("visibilitychange", reattach);
      controller.current?.abort();
      controller.current = null;
    };
  }, []);

  const ask = async (raw: string) => {
    const question = raw.trim();
    const attached = images.images;
    if ((!question && !attached.length) || live) return;
    const uploads = attached.length ? await images.prepare(attached) : [];
    if (uploads === null) return;
    // The live bubble shows the images by name until the saved turn brings them back.
    const shown = attached.map((image, index) => ({ id: "", name: uploads[index]?.name ?? image.file.name, mime_type: image.file.type }));
    images.clear();
    void follow(question, uploads, shown);
  };

  const stop = async () => {
    await api.assistant.stop().catch(() => undefined);
    controller.current?.abort();
  };

  const startOver = async () => {
    const confirmed = await confirmDialog("Clear this conversation? Your listings and sales are not touched.", {
      variant: "destructive",
      confirmLabel: "Clear conversation",
    });
    if (!confirmed) return;
    await api.assistant.clear();
    sessionFolds = [];
    setFolds([]);
    setError("");
    await queryClient.invalidateQueries({ queryKey: MESSAGES_KEY });
  };

  // Applied changes reach every page that shows a listing, a total or a box.
  const changesSettled = (message: AssistantMessage, applied: boolean) => {
    queryClient.setQueryData<AssistantMessage[]>(MESSAGES_KEY, (saved) =>
      saved?.map((turn) => (turn.id === message.id ? message : turn)));
    if (applied) void queryClient.invalidateQueries();
  };

  // Answers link the listings they name; open those in Studio instead of a new tab.
  const openLinkedListing = (event: MouseEvent<HTMLDivElement>) => {
    const link = (event.target as HTMLElement).closest("a");
    const id = linkedListingId(link?.getAttribute("href"));
    if (!id) return;
    event.preventDefault();
    const known = queryClient.getQueryData<{ id: string }[]>(["conversations"]);
    if (known && !known.some((listing) => listing.id === id)) {
      addToast({ type: "warning", title: "That listing is no longer in Studio." });
      return;
    }
    onOpenListing(id);
  };

  const drawn = live ? liveParts(live, turns) : { question: false, answer: false };
  const visible = live && drawn.answer ? stableStreamingText(live.parts.content) : "";
  const empty = !turns.length && !live && !messages.isLoading;
  const needsProvider = /sign in with chatgpt/i.test(error);
  const foldPositions = placeTurnFolds(turns, folds);
  const foldsAt = (index: number) => folds.filter((_, k) => foldPositions[k] === index);
  const liveTail = live && drawn.answer && !live.parts.trace.some((entry) => entry.kind === "phase")
    ? live.parts.status || "Answering…"
    : "";

  return (
    <div className="assistant-page flex min-h-0 min-w-0 flex-1 flex-col">
      <ChatTranscript scroll={scroll} laneClassName="max-w-208 px-4 pt-8 sm:px-8">
        <div className="contents" onClick={openLinkedListing}>
          {empty ? (
            <div className="pb-6">
              <h1 className="mb-2 font-semibold text-2xl text-foreground">Ask about your business</h1>
              <p className="text-muted-foreground text-sm leading-relaxed">
                The assistant reads every listing, sale, box, ad and sale event in Studio each time you ask. It
                answers questions, gives advice, and can fix listings for you: prices, costs, labels, titles, and
                sizes that differ between marketplace forms. You review every change before it is saved, and it
                never sends or publishes anything.
              </p>
              <ul className="mt-5 grid list-none grid-cols-[repeat(auto-fit,minmax(16rem,1fr))] gap-2 p-0">
                {ASSISTANT_STARTERS.map((starter) => (
                  <li key={starter}>
                    <button
                      type="button"
                      className="h-full w-full cursor-pointer rounded-xl border border-border/70 bg-card px-3.5 py-3 text-left text-secondary-label text-sm leading-snug transition-colors hover:border-border hover:bg-accent/40 hover:text-foreground"
                      onClick={() => { void ask(starter); }}
                    >
                      {starter}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
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
                  stopping={false}
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
          {error ? (
            <Alert variant="error" className="my-2">
              <CircleAlertIcon />
              <AlertTitle className="whitespace-pre-wrap">{error}</AlertTitle>
              {needsProvider ? (
                <AlertAction>
                  <Button type="button" size="xs" variant="outline" onClick={onOpenProviders}>
                    Open provider settings
                  </Button>
                </AlertAction>
              ) : null}
            </Alert>
          ) : null}
        </div>
      </ChatTranscript>
      <div className="mx-auto w-full max-w-208 sm:px-4">
        <ChatComposer
          composerRef={composerRef}
          input={input}
          onInputChange={setInput}
          placeholder="Ask a question, or ask for a change…"
          images={images}
          busy={Boolean(live)}
          onSend={() => { void ask(input); }}
          onStop={() => { void stop(); }}
          stopLabel="Stop answering"
          queueLabel={null}
          className="pb-2 sm:pb-2"
        />
        {noteOpen ? <BusinessNote onClose={() => setNoteOpen(false)} /> : null}
        <div className="flex justify-between gap-3 px-5 pb-3 sm:px-6">
          <Button type="button" size="xs" variant="ghost-muted" aria-expanded={noteOpen} onClick={() => setNoteOpen((open) => !open)}>
            About your business
          </Button>
          {turns.length && !live ? (
            <Button type="button" size="xs" variant="ghost-muted" onClick={() => { void startOver(); }}>
              Clear conversation
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * What the seller wants the assistant to keep in mind — goals, margins, what
 * they will not source — sent along with every question.
 */
function BusinessNote({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const saved = useQuery({ queryKey: NOTE_KEY, queryFn: api.assistant.note });
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (note: string) => api.assistant.saveNote(note),
    onSuccess: (result) => {
      queryClient.setQueryData(NOTE_KEY, result);
      onClose();
    },
  });
  const note = draft ?? saved.data?.note ?? "";

  return (
    <form
      className="mx-3 mb-2 flex flex-col gap-2 rounded-xl border border-border/70 bg-card p-3.5 sm:mx-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(note);
      }}
    >
      <label htmlFor="assistant-note-text" className="font-medium text-foreground text-sm">About your business</label>
      <p className="text-muted-foreground text-xs leading-relaxed">
        Anything the assistant should keep in mind on every question: your goals, the margin you want, how much time
        you have, what you will not source, where you ship from.
      </p>
      <Textarea
        id="assistant-note-text"
        rows={5}
        maxLength={NOTE_MAX}
        value={note}
        disabled={saved.isLoading}
        placeholder="I want at least $15 profit per item and sell mostly vintage menswear. I list about 20 items a week."
        onChange={(event) => setDraft(event.target.value)}
      />
      {save.isError ? <p className="text-destructive-foreground text-sm">{(save.error as Error).message || "Could not save."}</p> : null}
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit" size="sm" disabled={save.isPending || saved.isLoading}>Save</Button>
      </div>
    </form>
  );
}
