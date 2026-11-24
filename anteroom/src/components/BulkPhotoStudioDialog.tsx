import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { refreshInventory } from "../api/inventoryRefresh";
import { scheduledDraftsQuery } from "../api/pageQueries";
import { ApiRequestError } from "../api/transport";
import type { ListingPhoto } from "../api/types";
import { BULK_READ_CONCURRENCY, mapWithConcurrency } from "../lib/concurrency";
import { cn } from "../lib/utils";
import { MAX_STUDIO_PHOTOS, studioProblem } from "../photoEdits";
import { photoThumbUrl, photoVersion } from "../photoThumb";
import { listingTab } from "./inventoryFilters";
import { defaultStudioChoice, studioRequest, StudioOptionsFields } from "./PhotoStudioOptions";
import { PriceDialog } from "./PriceDropParts";
import type { Listing } from "./sidebar/listingShelves";
import { StudioImage } from "./StudioImage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Spinner } from "./ui/spinner";
import { PageError, PageNote } from "./WorkspacePage";

const EDITABLE_PHOTOS = new Set(["image/jpeg", "image/png", "image/webp"]);
const isDraft = (item: Listing) => item.status === "draft" && listingTab(item) === "draft";

interface DraftPhotos {
  id: string;
  title: string;
  photos: ListingPhoto[];
  schedule: string | null;
  skipped?: string;
}

async function readDraftPhotos(listings: Listing[], signal: AbortSignal): Promise<DraftPhotos[]> {
  const schedules = listings.some(isDraft) ? await api.automations.scheduledDrafts({ signal }) : [];
  const byId = new Map(schedules.map((schedule) => [schedule.conversation_id, schedule]));
  return mapWithConcurrency(listings, BULK_READ_CONCURRENCY, async (item) => {
    signal.throwIfAborted();
    const row = { id: item.id, title: item.title || "Untitled draft", photos: [], schedule: null };
    if (!isDraft(item)) return { ...row, skipped: "Only drafts can be edited in bulk." };
    const schedule = byId.get(item.id);
    if (schedule?.scheduled_status === "publishing") return { ...row, skipped: "This draft is publishing." };
    try {
      const photos = await api.conversations.photos(item.id, undefined, { signal });
      return {
        ...row, photos,
        schedule: schedule?.scheduled_status === "scheduled" ? schedule.scheduled_confirmed_at : null,
        skipped: photos.some((photo) => EDITABLE_PHOTOS.has(photo.mime_type)) ? undefined : "No editable photos.",
      };
    } catch (error) {
      signal.throwIfAborted();
      return { ...row, skipped: error instanceof Error ? error.message : "Couldn't load photos." };
    }
  });
}

interface PhotoResult {
  state: "saved" | "failed" | "skipped" | "not sent";
  detail?: string;
  needsReview?: boolean;
}

interface Target {
  draft: DraftPhotos;
  photos: ListingPhoto[];
}

interface BulkPhotoBatch {
  id: number;
  listings: Listing[];
}

/**
 * Kept at the workspace level so closing a dialog or opening a draft leaves its
 * batch running. Each batch runs on its own, so the seller can start another for
 * other drafts while earlier ones are still working.
 */
export function BulkPhotoBatches({ batches, onDone }: {
  batches: BulkPhotoBatch[];
  onDone: (id: number) => void;
}) {
  return batches.map((batch, slot) => (
    <BulkPhotoStudioDialog key={batch.id} batchId={batch.id} slot={slot} listings={batch.listings} onDone={() => onDone(batch.id)} />
  ));
}

function BulkPhotoStudioDialog({ batchId, slot = 0, listings, onDone }: {
  batchId: number;
  /** Stacks this batch's background button above earlier batches' buttons. */
  slot?: number;
  listings: Listing[];
  onDone: () => void;
}) {
  const client = useQueryClient();
  const [step, setStep] = useState<"options" | "review" | "results">("options");
  const [visible, setVisible] = useState(true);
  const [choice, setChoice] = useState(defaultStudioChoice);
  const [selection, setSelection] = useState<Set<string> | null>(null);
  const [results, setResults] = useState<Map<string, PhotoResult>>(new Map());
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const active = useRef<{ controller: AbortController; stop: boolean } | null>(null);
  useEffect(() => () => active.current?.controller.abort(), []);

  const key = useQuery({ queryKey: ["settings-photoroom"], queryFn: api.settings.photoroom, staleTime: 60_000 });
  const review = useQuery({
    queryKey: ["bulk-photo-review", batchId, listings.map((item) => item.id)],
    queryFn: ({ signal }) => readDraftPhotos(listings, signal),
    staleTime: Infinity, gcTime: 0, retry: false,
  });
  const rows = review.data;
  const covers = useMemo(() => new Set(rows?.flatMap((draft) => {
    const photo = draft.photos[0];
    return photo && EDITABLE_PHOTOS.has(photo.mime_type) && !photo.edits?.studio ? [photo.id] : [];
  })), [rows]);
  const chosen = selection ?? covers;
  const targets = useMemo(() => rows?.flatMap((draft) => {
    const photos = draft.photos.filter((photo) => chosen.has(photo.id));
    return photos.length ? [{ draft, photos }] : [];
  }) ?? [], [rows, chosen]);
  const photoCount = targets.reduce((count, target) => count + target.photos.length, 0);
  const options = studioRequest(choice);
  const problem = studioProblem(options);
  const saved = Array.from(results.values()).filter((result) => result.state === "saved").length;
  const failed = Array.from(results.values()).filter((result) => result.state === "failed").length;
  const notSent = Array.from(results.values()).filter((result) => result.state === "not sent").length;
  const skipped = results.size - saved - failed - notSent;
  const summary = [`${saved} saved`, `${failed} failed`, ...(skipped ? [`${skipped} skipped`] : []),
    ...(notSent ? [`${notSent} not sent`] : [])].join(" · ");

  const apply = async (work: Target[]) => {
    if (active.current || !work.length) return;
    const run = { controller: new AbortController(), stop: false };
    active.current = run;
    setStep("results");
    setRunning(true);
    setStopping(false);
    setResults((previous) => {
      const next = new Map(previous);
      for (const { photos } of work) for (const photo of photos) next.delete(photo.id);
      return next;
    });
    const { signal } = run.controller;
    let pause: string | null = null;
    const requests: Target[] = [];
    for (const { draft, photos } of work) {
      for (let start = 0; start < photos.length; start += MAX_STUDIO_PHOTOS) {
        requests.push({ draft, photos: photos.slice(start, start + MAX_STUDIO_PHOTOS) });
      }
    }
    try {
      // Each request already runs several photos in parallel. Queue drafts here
      // so selecting more listings doesn't multiply Photoroom concurrency.
      for (const { draft, photos } of requests) {
        if (signal.aborted) return;
        const outcome = new Map<string, PhotoResult>();
        if (run.stop || pause) {
          for (const photo of photos) outcome.set(photo.id, { state: "not sent", detail: pause || "Stopped before sending." });
        } else {
          try {
            const answer = await api.conversations.photoStudio(draft.id, photos.map((photo) => photo.id), options, signal, {
              photo_versions: Object.fromEntries(photos.map((photo) => [photo.id, photoVersion(photo.url)])),
              scheduled_confirmed_at: draft.schedule,
            });
            for (const photo of answer.photos) outcome.set(photo.id, { state: "saved" });
            for (const error of answer.errors) outcome.set(error.photo_id, {
              state: error.needs_review ? "skipped" : "failed", detail: error.detail, needsReview: error.needs_review,
            });
            if (answer.provider_blocked) pause = "Photoroom stopped accepting edits. Check your plan or API key.";
          } catch (error) {
            const refused = error instanceof ApiRequestError && error.status === 400;
            const detail = refused ? error.message : "Couldn't confirm the edit. Check the draft before trying again.";
            for (const photo of photos) outcome.set(photo.id, {
              state: refused ? "failed" : "skipped", detail, needsReview: !refused,
            });
            if (refused) pause = detail;
          }
          if (signal.aborted) return;
          for (const name of ["photos", "draft-schedule"]) {
            void client.invalidateQueries({ queryKey: [name, draft.id] });
          }
          refreshInventory(client);
        }
        setResults((previous) => new Map([...previous, ...outcome]));
      }
    } finally {
      active.current = null;
      if (!signal.aborted) {
        void client.invalidateQueries({ queryKey: scheduledDraftsQuery.queryKey });
        setRunning(false);
      }
    }
  };

  const retry = () => void apply(targets.map(({ draft, photos }) => ({
    draft, photos: photos.filter((photo) => {
      const state = results.get(photo.id)?.state;
      return state === "failed" || state === "not sent";
    }),
  })).filter(({ photos }) => photos.length > 0));
  const reviewAgain = async () => {
    const previousVersions = new Map(targets.flatMap(({ photos }) => photos
      .filter((photo) => results.get(photo.id)?.needsReview)
      .map((photo) => [photo.id, photoVersion(photo.url)] as const)));
    setSelection(new Set());
    setResults(new Map());
    setStep("review");
    const refreshed = await review.refetch();
    if (!refreshed.isSuccess) return;
    const photos = refreshed.data.flatMap((draft) => draft.photos);
    setSelection(new Set(photos.filter((photo) =>
      EDITABLE_PHOTOS.has(photo.mime_type) && !photo.edits?.studio
      && previousVersions.get(photo.id) === photoVersion(photo.url),
    ).map((photo) => photo.id)));
  };
  const close = () => running ? setVisible(false) : onDone();
  if (!visible) return (
    <Button className="fixed right-4 z-20" style={{ bottom: `calc(7rem + ${slot} * 2.75rem)` }} variant="outline" onClick={() => setVisible(true)}>
      {running ? `Photo edits · ${results.size} of ${photoCount}` : "Photo edit results"}
    </Button>
  );

  return (
    <PriceDialog title={step === "results" ? "Photo edit results" : "Edit photos"} className="max-w-2xl max-sm:[--glass-opacity:100%]" onClose={close}
      description={step === "options" ? "Choose one Photoroom edit for the selected drafts." : "Edits save to draft photos in Anteroom."}
      footer={step === "options" ? <>
        <Button variant="outline" onClick={onDone}>Cancel</Button>
        <Button disabled={Boolean(problem) || !key.data?.available} onClick={() => setStep("review")}>Review photos</Button>
      </> : step === "review" ? <>
        <Button variant="ghost" onClick={() => setStep("options")}>Back to edit</Button>
        <Button disabled={review.isFetching || review.isError || !photoCount} onClick={() => void apply(targets)}>Apply to {photoCount} {photoCount === 1 ? "photo" : "photos"}</Button>
      </> : running ? <>
        <Button variant="outline" disabled={stopping} onClick={() => {
          if (active.current) active.current.stop = true;
          setStopping(true);
        }}>{stopping ? "Finishing current photos…" : "Stop batch"}</Button>
        <Button onClick={close}>Continue in background</Button>
      </> : <>
        {Array.from(results.values()).some((result) => result.needsReview) && <Button variant="outline" onClick={() => void reviewAgain()}>Review again</Button>}
        {(failed > 0 || notSent > 0) && <Button variant="outline" onClick={retry}>{notSent ? "Resume batch" : "Retry failed"}</Button>}
        <Button onClick={onDone}>Done</Button>
      </>}>
      {step === "options" && <>
        {key.isError && <PageError message={key.error.message} />}
        {key.data && !key.data.available && <PageNote>Add a Photoroom API key in Settings → Providers to use AI Studio.</PageNote>}
        <StudioOptionsFields choice={choice} onChange={setChoice} />
        {problem && <PageNote>{problem}</PageNote>}
      </>}
      {step === "review" && <>
        {review.isFetching && <p role="status" className="flex items-center gap-2"><Spinner /> Loading photos…</p>}
        {review.isError && <>
          <PageError message={review.error.message} />
          <Button variant="outline" onClick={() => void review.refetch()}>Retry loading photos</Button>
        </>}
        {rows && !review.isFetching && !review.isError && <>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setSelection(null)}>Cover photos</Button>
            <Button variant="outline" size="sm" onClick={() => setSelection(new Set(
              rows.flatMap((draft) => draft.photos.filter((photo) => EDITABLE_PHOTOS.has(photo.mime_type)).map((photo) => photo.id)),
            ))}>All photos</Button>
            <p className="text-xs text-muted-foreground tabular-nums" role="status">{photoCount} selected across {targets.length} {targets.length === 1 ? "draft" : "drafts"}</p>
          </div>
          <PageNote>Each selected photo is a separate Photoroom edit.</PageNote>
          <div className="divide-y divide-border rounded-xl border border-border">
            {rows.map((draft) => (
              <DraftPhotoChoices key={draft.id} draft={draft} selected={chosen} onToggle={(id, checked) => {
                setSelection((previous) => {
                  const next = new Set(previous ?? covers);
                  if (checked) next.add(id);
                  else next.delete(id);
                  return next;
                });
              }} />
            ))}
          </div>
        </>}
      </>}
      {step === "results" && <>
        <p role="status" className="flex items-center gap-2 font-medium tabular-nums">
          {running && <Spinner />}
          {running ? `${results.size} of ${photoCount} photos processed` : summary}
        </p>
        <ul className="divide-y divide-border rounded-xl border border-border">
          {targets.map(({ draft, photos }) => {
            const count = photos.filter((photo) => results.get(photo.id)?.state === "saved").length;
            return <li key={draft.id} className="space-y-1 p-3">
              <p className="font-medium">{draft.title}</p>
              {count > 0 && <p className="text-xs text-muted-foreground">{count} saved{draft.schedule ? " · Reschedule before publishing" : ""}</p>}
              {photos.map((photo) => {
                const result = results.get(photo.id);
                if (!result?.detail) return null;
                const label = photoLabel(draft.photos.indexOf(photo));
                return <p key={photo.id} className={cn("text-xs", result.state === "failed" ? "text-destructive-foreground" : "text-muted-foreground")}>
                  {label} · {result.detail}
                </p>;
              })}
            </li>;
          })}
        </ul>
      </>}
    </PriceDialog>
  );
}

const photoLabel = (index: number) => index === 0 ? "Cover photo" : `Photo ${index + 1}`;

function DraftPhotoChoices({ draft, selected, onToggle }: {
  draft: DraftPhotos;
  selected: ReadonlySet<string>;
  onToggle: (id: string, checked: boolean) => void;
}) {
  return (
    <div role="group" aria-label={draft.title} className="min-w-0 space-y-2 p-3">
      <p className="break-words font-medium">{draft.title}</p>
      {draft.skipped ? <PageNote>{draft.skipped}</PageNote> : <>
        {draft.schedule && <p className="text-xs text-warning-foreground">Editing these photos clears the publishing schedule.</p>}
        <div className="grid grid-cols-[repeat(auto-fill,minmax(72px,1fr))] gap-2">
          {draft.photos.map((photo, index) => {
            const checked = selected.has(photo.id);
            const editable = EDITABLE_PHOTOS.has(photo.mime_type);
            const label = photoLabel(index);
            return (
              <label key={photo.id} className={cn("relative min-w-0 overflow-hidden rounded-lg border", checked ? "border-foreground" : "border-border", editable && "cursor-pointer")}>
                <StudioImage src={photoThumbUrl(photo)} alt="" loading="lazy" decoding="async" className="aspect-square w-full bg-muted object-cover" />
                <Checkbox aria-label={label} className="absolute left-1 top-1 bg-background dark:not-data-checked:bg-background" checked={checked}
                  disabled={!editable} onCheckedChange={(value) => onToggle(photo.id, value === true)} />
                <span className="block px-1.5 py-1 text-xs">{label}</span>
                {(photo.edits?.studio || !editable) && <span className="block px-1.5 pb-1 text-xs text-muted-foreground">
                  {editable ? "AI edited" : "Unsupported"}
                </span>}
              </label>
            );
          })}
        </div>
      </>}
    </div>
  );
}
