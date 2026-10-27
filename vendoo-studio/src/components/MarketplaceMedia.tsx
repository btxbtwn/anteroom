/**
 * The photos and video at the top of one marketplace's form, laid out like
 * Vendoo's Photo Manager and the marketplaces' own forms: the tiles it sends
 * in order, then the item photos it leaves off. Photos and a video are added
 * the same way, by dropping or choosing files, and a video only where the
 * marketplace takes one.
 */
import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ImagePlusIcon, PlayIcon, PlusIcon, RotateCcwIcon, XIcon } from "lucide-react";
import { api } from "../api/client";
import { videoProblem, type ListingVideo } from "../api/listingVideos";
import type { ListingData, Photo } from "../api/types";
import { cn } from "../lib/utils";
import { cloneListing, formFieldText, getListingEditorValue, setListingEditorValue } from "../listingPaths";
import { formPhotos, isVideoFile, videoRuleText, withPick } from "../marketplaceMedia";
import { dragHasFiles, isImageFile, moveItem } from "../photoDrop";
import { InspectorSection } from "./ListingFormControls";
import { marketplaceName } from "./marketplaceNames";
import { PhotoEditor } from "./PhotoEditor";
import { PhotoLightbox, PhotoStrip, PhotoThumb } from "./PhotoThumbs";
import { useListingSessionActive } from "./listingSession";
import { photoThumbUrl } from "../photoThumb";
import { Button } from "./ui/button";

const DRAG_TYPE = "application/x-vendoo-form-photo-id";

export function MarketplaceMedia({ convId, marketplace, listing, disabled, onChange }: {
  convId: string;
  marketplace: string;
  listing: ListingData;
  disabled?: boolean;
  onChange: (listing: ListingData) => void;
}) {
  const queryClient = useQueryClient();
  const input = React.useRef<HTMLInputElement>(null);
  const dragRef = React.useRef<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [dropping, setDropping] = React.useState(false);
  const [dragId, setDragId] = React.useState<string | null>(null);
  const [dropId, setDropId] = React.useState<string | null>(null);
  const [previewId, setPreviewId] = React.useState<string | null>(null);
  const [editingId, setEditingId] = React.useState<string | null>(null);
  const name = marketplaceName(marketplace);
  const ownKey = ["photos", convId, marketplace];
  const { data: item = [] } = useQuery({ queryKey: ["photos", convId], queryFn: () => api.conversations.photos(convId) });
  const { data: own = [] } = useQuery({ queryKey: ownKey, queryFn: () => api.conversations.photos(convId, marketplace) });
  const { data: rules } = useQuery({ queryKey: ["marketplace-media"], queryFn: api.marketplaceMedia.rules, staleTime: Infinity });
  const videoRules = rules?.video_rules?.[marketplace];
  const { data: library } = useQuery({
    queryKey: ["listing-videos", convId], queryFn: () => api.listingVideos.list(convId), enabled: Boolean(videoRules),
  });
  const sessionActive = useListingSessionActive();
  const { picked, unticked, custom } = formPhotos(listing, item, own, marketplace);
  const ids = picked.map((photo) => photo.id);
  const limit = rules?.photo_limits?.[marketplace];
  const videoKey = `${marketplace}_specifics.videoId`;
  const videoId = videoRules ? formFieldText(getListingEditorValue(listing, videoKey)) : "";
  const video = library?.videos.find((entry) => entry.id === videoId);
  const otherVideos = videoRules ? (library?.videos || []).filter((entry) => entry.id !== videoId) : [];
  const locked = disabled || busy;
  const all = [...picked, ...unticked];
  const previewIndex = all.findIndex((photo) => photo.id === previewId);
  const editing = all.find((photo) => photo.id === editingId) || null;

  const setPick = (next: string[]) => {
    if (!next.length) {
      setError(`${name} needs at least one photo.`);
      return;
    }
    setError(null);
    onChange(withPick(listing, marketplace, next, item));
  };
  const withVideo = (base: ListingData, id: string) => {
    const updated = cloneListing(base);
    setListingEditorValue(updated, videoKey, id);
    return updated;
  };
  const refreshPhotos = () => queryClient.invalidateQueries({ queryKey: ["photos", convId] });

  const addFiles = async (files: File[]) => {
    const images = files.filter((file) => isImageFile(file));
    const videos = videoRules ? files.filter((file) => isVideoFile(file)) : [];
    if (!images.length && !videos.length) {
      setError(videoRules ? "Choose photos or a video." : `${name} takes photos only, not video.`);
      return;
    }
    setBusy(true);
    setError(null);
    const problems: string[] = [];
    try {
      let updated = listing;
      if (images.length) {
        const result = await api.conversations.uploadPhotos(convId, images, marketplace);
        await queryClient.invalidateQueries({ queryKey: ownKey });
        problems.push(...(result.errors || []));
        updated = withPick(updated, marketplace, [...ids, ...result.photos.map((photo) => photo.id)], item);
      }
      if (videos.length) {
        if (videos.length > 1) problems.push(`${name} takes one video. Only ${videos[0].name} was added.`);
        const uploaded = await api.listingVideos.upload(convId, videos[0]);
        await queryClient.invalidateQueries({ queryKey: ["listing-videos", convId] });
        const problem = videoProblem(uploaded, videoRules);
        if (problem) problems.push(problem);
        else updated = withVideo(updated, uploaded.id);
      }
      if (updated !== listing) onChange(updated);
    } catch (err) {
      problems.push(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
      setError(problems.length ? problems.join(" ") : null);
      if (input.current) input.current.value = "";
    }
  };

  const deleteOwn = async (photo: Photo) => {
    setBusy(true);
    try {
      await api.conversations.deletePhoto(convId, photo.id);
      if (ids.includes(photo.id)) onChange(withPick(listing, marketplace, ids.filter((id) => id !== photo.id), item));
      await queryClient.invalidateQueries({ queryKey: ownKey });
    } catch (err) {
      setError(err instanceof Error ? err.message : "The photo could not be deleted.");
    } finally {
      setBusy(false);
    }
  };

  const deleteVideo = async (entry: ListingVideo) => {
    setBusy(true);
    try {
      await api.listingVideos.delete(convId, entry.id);
      await queryClient.invalidateQueries({ queryKey: ["listing-videos", convId] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "The video could not be deleted.");
    } finally {
      setBusy(false);
    }
  };

  const rotate = async (photoId: string, degrees: 90 | -90) => {
    try {
      await api.conversations.rotatePhoto(convId, photoId, degrees);
      await refreshPhotos();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rotate failed");
    }
  };

  const resetDrag = () => {
    dragRef.current = null;
    setDragId(null);
    setDropId(null);
  };

  const onStripDragOver = (event: React.DragEvent) => {
    if (!dragHasFiles(event.dataTransfer) || locked) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    if (!dropping) setDropping(true);
  };
  const onStripDrop = (event: React.DragEvent) => {
    setDropping(false);
    if (!dragHasFiles(event.dataTransfer)) return;
    // Handled here, so the window's drop does not add them to the item's photos.
    event.preventDefault();
    if (locked) return;
    void addFiles(Array.from(event.dataTransfer.files || []));
  };

  const thumbs = picked.map((photo, index) => {
    const overLimit = limit !== undefined && index >= limit;
    return <PhotoThumb
      key={photo.id}
      url={photoThumbUrl(photo)}
      index={index}
      reorderable={!locked && picked.length > 1}
      draggable={!locked && picked.length > 1}
      dragging={dragId === photo.id}
      dropTarget={dropId === photo.id}
      className={cn(overLimit && "opacity-50")}
      badge={overLimit ? "Not sent" : photo.marketplace ? name : undefined}
      title={overLimit ? `${name} keeps ${limit} photos; this one is not sent.` : "Drag to set the order on this form"}
      onDragStart={(event) => {
        dragRef.current = photo.id;
        setDragId(photo.id);
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(DRAG_TYPE, photo.id);
      }}
      onDragOver={(event) => {
        if (!dragRef.current || dragRef.current === photo.id) return;
        event.preventDefault();
        event.stopPropagation();
        if (dropId !== photo.id) setDropId(photo.id);
      }}
      onDrop={(event) => {
        if (dragHasFiles(event.dataTransfer)) return;
        event.preventDefault();
        event.stopPropagation();
        const source = dragRef.current;
        resetDrag();
        if (!source) return;
        const next = moveItem(ids, ids.indexOf(source), index);
        if (next !== ids) setPick(next);
      }}
      onDragEnd={resetDrag}
      onOpen={() => setPreviewId(photo.id)}
      onRemove={locked ? undefined : () => setPick(ids.filter((id) => id !== photo.id))}
      removeLabel={`Leave photo ${index + 1} off ${name}`}
      controlsInViewerOnTouch
    />;
  });

  if (!sessionActive) return null;

  return (
    <InspectorSection
      data-marketplace-media={marketplace}
      title="Photos"
      meta={`${Math.min(picked.length, limit ?? picked.length)}${limit ? ` of ${limit}` : ""} sent to ${name}${custom ? " · this form's own" : " · the item's photos"}`}
      actions={<>
        {custom && <Button variant="ghost" size="xs" disabled={locked} title="Send the item's photos in item order"
          onClick={() => setPick(item.map((photo) => photo.id))}>
          <RotateCcwIcon />
          Reset to item photos
        </Button>}
        <Button variant="outline" size="xs" disabled={locked} onClick={() => input.current?.click()}>
          <ImagePlusIcon />
          {busy ? "Adding…" : videoRules ? "Add photos or video" : "Add photos"}
        </Button>
        <input ref={input} type="file" multiple hidden aria-label={`Add photos${videoRules ? " or a video" : ""} to ${name}`}
          accept={videoRules ? "image/*,video/*" : "image/*"}
          onChange={(event) => { if (event.target.files) void addFiles(Array.from(event.target.files)); }} />
      </>}
    >
      <div
        onDragOver={onStripDragOver}
        onDragLeave={() => setDropping(false)}
        onDrop={onStripDrop}
        className={cn("min-w-0 space-y-2 rounded-lg", dropping && "ring-2 ring-ring")}
      >
        <PhotoStrip>
          {video && <VideoTile video={video} label={`${name} video`} problem={videoProblem(video, videoRules)}
            onRemove={locked ? undefined : () => onChange(withVideo(listing, ""))} />}
          {videoId && library && !video && <p role="alert" className="text-xs text-destructive">The selected video is missing. Add another video or remove it.</p>}
          {thumbs}
          <button type="button" disabled={locked} onClick={() => input.current?.click()}
            className="flex size-16 shrink-0 items-center justify-center rounded-lg border border-dashed text-muted-foreground hover:border-ring/60 disabled:opacity-50"
            aria-label={videoRules ? `Add photos or a video to ${name}` : `Add photos to ${name}`}
            title={videoRules ? "Drop or choose photos or a video" : "Drop or choose photos"}>
            <PlusIcon className="size-4" />
          </button>
        </PhotoStrip>
        {(unticked.length > 0 || otherVideos.length > 0) && <div className="space-y-1">
          <p className="text-xs text-muted-foreground">Not on {name}. Add one back with +.</p>
          <PhotoStrip>
            {otherVideos.map((entry) => {
              const problem = videoProblem(entry, videoRules);
              return <VideoTile key={entry.id} video={entry} label={entry.original_filename} problem={problem} muted
                onAdd={locked || problem ? undefined : () => onChange(withVideo(listing, entry.id))}
                onRemove={locked ? undefined : () => void deleteVideo(entry)} removeLabel={`Delete video ${entry.original_filename}`} />;
            })}
            {unticked.map((photo) => <PhotoThumb
              key={photo.id}
              url={photoThumbUrl(photo)}
              index={all.indexOf(photo)}
              numbered={false}
              className="opacity-60"
              badge={photo.marketplace ? name : undefined}
              onOpen={() => setPreviewId(photo.id)}
              onAdd={locked ? undefined : () => setPick([...ids, photo.id])}
              addLabel={`Send this photo to ${name}`}
              onRemove={locked || !photo.marketplace ? undefined : () => void deleteOwn(photo)}
              removeLabel={`Delete this ${name} photo`}
              controlsInViewerOnTouch
            />)}
          </PhotoStrip>
        </div>}
        <p className="text-xs text-muted-foreground">
          {limit ? `${name} takes up to ${limit} photos. ` : ""}
          {videoRules ? videoRuleText(videoRules) : ""}
          {custom ? " Photos added to the item later show here, left off, until you add them." : ""}
        </p>
        {limit !== undefined && picked.length > limit && <p role="status" className="text-xs text-warning-foreground">
          {name} keeps {limit} photos. The first {limit} of {picked.length} are sent. Leave some off or drag the ones you want first.
        </p>}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      </div>
      {previewIndex >= 0 && <PhotoLightbox
        urls={all.map((photo) => photo.url)}
        index={previewIndex}
        onStep={(next) => setPreviewId(all[next].id)}
        onRotate={(degrees) => void rotate(all[previewIndex].id, degrees)}
        edited={Boolean(all[previewIndex].edits)}
        onEdit={() => { setEditingId(all[previewIndex].id); setPreviewId(null); }}
        onMove={previewIndex < picked.length && !locked ? (delta) => {
          const next = moveItem(ids, previewIndex, previewIndex + delta);
          if (next !== ids) setPick(next);
        } : undefined}
        onClose={() => setPreviewId(null)}
      />}
      {editing && <PhotoEditor key={editing.id} convId={convId} photo={editing} index={all.indexOf(editing)}
        onSaved={() => void refreshPhotos()}
        onClose={() => { setPreviewId(editing.id); setEditingId(null); }} />}
    </InspectorSection>
  );
}

function VideoTile({ video, label, problem, muted, onAdd, onRemove, removeLabel }: {
  video: ListingVideo;
  label: string;
  problem: string | null;
  muted?: boolean;
  onAdd?: () => void;
  onRemove?: () => void;
  removeLabel?: string;
}) {
  return (
    <div className={cn("group/thumb relative size-16 shrink-0 overflow-hidden rounded-lg border bg-muted", (muted || problem) && "opacity-60")}
      title={problem || `${video.original_filename} · ${video.duration_seconds.toFixed(1)} seconds`}>
      <video src={video.url} preload="metadata" muted playsInline aria-label={label} className="size-full object-cover" />
      <span className="pointer-events-none absolute bottom-0.5 left-0.5 flex items-center gap-0.5 rounded-sm bg-black/60 px-1 text-[9px] text-white/90 leading-3.5 tabular-nums">
        <PlayIcon className="size-2" />
        {video.duration_seconds.toFixed(0)}s
      </span>
      {problem && <span className="pointer-events-none absolute inset-x-0 top-0 bg-destructive/80 px-1 text-[9px] text-white leading-3.5">Not accepted</span>}
      {onAdd && <Button variant="overlay" size="icon-micro" className="absolute right-0.5 bottom-0.5" aria-label={`Use ${label}`} title="Use this video" onClick={onAdd}>
        <PlusIcon />
      </Button>}
      {onRemove && <Button variant="overlay" size="icon-micro" aria-label={removeLabel ?? `Remove ${label}`} title={removeLabel ?? "Remove video"}
        className="absolute top-0.5 right-0.5 opacity-0 transition-opacity focus-visible:opacity-100 group-hover/thumb:opacity-100 pointer-coarse:opacity-100"
        onClick={onRemove}>
        <XIcon />
      </Button>}
    </div>
  );
}
