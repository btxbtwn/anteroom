import React, { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { dragHasFiles, groupImageFilesByFolder, moveItem, type PhotoFolderGroup } from "../photoDrop";
import type { Photo } from "../api/types";
import { toastManager } from "./ui/toast";
import { BulkUploadDialog } from "./BulkUploadDialog";
import { InspectorSection } from "./ListingFormControls";
import { PhotoLightbox, PhotoStrip, PhotoThumb } from "./PhotoThumbs";
import { Button } from "./ui/button";
import { FolderPlusIcon, ImagePlusIcon } from "lucide-react";
import {
  createBulkPhotoListings,
  type BulkListingUploadResult,
  type BulkUploadDefaults,
  type BulkUploadItem,
} from "../bulkPhotoUpload";

const PHOTO_DRAG_TYPE = "application/x-vendoo-photo-id";

interface Props {
  convId: string;
  /** When a folder pick expands into several item folders, App can focus the first draft. */
  onBulkListingsCreated?: (listings: BulkListingUploadResult[], generate: boolean) => void;
}

export function PhotoTray({ convId, onBulkListingsCreated }: Props) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const suppressClickRef = useRef(false);
  const dragIdRef = useRef<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);
  const [pendingBulkGroups, setPendingBulkGroups] = useState<PhotoFolderGroup[] | null>(null);

  const { data: photos } = useQuery({
    queryKey: ["photos", convId],
    queryFn: () => api.conversations.photos(convId),
  });

  const deleteMutation = useMutation({
    mutationFn: (photoId: string) => api.conversations.deletePhoto(convId, photoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["photos", convId] });
      // The sidebar thumbnail comes from the conversation list's cover photo.
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const rotateMutation = useMutation({
    mutationFn: ({ photoId, degrees }: { photoId: string; degrees: 90 | -90 }) =>
      api.conversations.rotatePhoto(convId, photoId, degrees),
    onSuccess: (rotated) => {
      queryClient.setQueryData<Photo[]>(["photos", convId], (current) =>
        current?.map((photo) => (photo.id === rotated.id ? rotated : photo)),
      );
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
    onError: (err: Error) => setUploadError(err.message || "Rotate failed"),
  });
  const rotatingId = rotateMutation.isPending ? rotateMutation.variables?.photoId : null;
  const rotate = (photoId: string, degrees: 90 | -90) => {
    if (rotateMutation.isPending) return;
    setUploadError(null);
    rotateMutation.mutate({ photoId, degrees });
  };

  const reorderMutation = useMutation({
    mutationFn: (orderedIds: string[]) => api.conversations.reorderPhotos(convId, orderedIds),
    onMutate: async (orderedIds) => {
      await queryClient.cancelQueries({ queryKey: ["photos", convId] });
      const previous = queryClient.getQueryData<Photo[]>(["photos", convId]);
      if (previous) {
        const byId = new Map(previous.map((photo) => [photo.id, photo]));
        queryClient.setQueryData(
          ["photos", convId],
          orderedIds.map((id) => byId.get(id)).filter((photo): photo is Photo => Boolean(photo)),
        );
      }
      return { previous };
    },
    onError: (err: Error, _orderedIds, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["photos", convId], context.previous);
      }
      setUploadError(err.message || "Reorder failed");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["photos", convId] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const previewIndex = photos?.findIndex((p) => p.id === previewId) ?? -1;
  const previewPhoto = photos && previewIndex >= 0 ? photos[previewIndex] : null;

  useEffect(() => {
    setPreviewId(null);
    setDragId(null);
    setDropId(null);
  }, [convId]);

  useEffect(() => {
    if (previewId && photos && !photos.some((p) => p.id === previewId)) {
      setPreviewId(null);
    }
  }, [photos, previewId]);

  const canReorder = (photos?.length || 0) > 1;
  // A phone cannot drag thumbnails or drop files; it reorders from the photo viewer.
  const touchScreen = typeof window !== "undefined" && Boolean(window.matchMedia?.("(hover: none)").matches);

  const resetDrag = () => {
    dragIdRef.current = null;
    setDragId(null);
    setDropId(null);
    window.setTimeout(() => {
      suppressClickRef.current = false;
    }, 150);
  };

  const onThumbDragStart = (event: React.DragEvent<HTMLDivElement>, photoId: string) => {
    if (!canReorder) {
      event.preventDefault();
      return;
    }
    suppressClickRef.current = true;
    dragIdRef.current = photoId;
    setDragId(photoId);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(PHOTO_DRAG_TYPE, photoId);
    event.dataTransfer.setData("text/plain", photoId);
  };

  const onThumbDragOver = (event: React.DragEvent<HTMLDivElement>, photoId: string) => {
    const sourceId = dragIdRef.current;
    if (!sourceId || sourceId === photoId) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "move";
    if (dropId !== photoId) setDropId(photoId);
  };

  const onThumbDrop = (event: React.DragEvent<HTMLDivElement>, photoId: string) => {
    // Files dropped on a thumbnail are an upload, not a reorder — let the
    // window-level drop handler take them.
    if (dragHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    const sourceId = dragIdRef.current || event.dataTransfer.getData(PHOTO_DRAG_TYPE) || event.dataTransfer.getData("text/plain");
    resetDrag();
    if (!photos || !sourceId || sourceId === photoId) return;
    const from = photos.findIndex((photo) => photo.id === sourceId);
    const to = photos.findIndex((photo) => photo.id === photoId);
    const next = moveItem(photos, from, to);
    if (next === photos) return;
    setUploadError(null);
    reorderMutation.mutate(next.map((photo) => photo.id));
  };

  const openPreview = (photoId: string) => {
    if (suppressClickRef.current) return;
    setPreviewId(photoId);
  };

  const resetInputs = () => {
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (folderInputRef.current) folderInputRef.current.value = "";
  };

  const doBulkUpload = async (items: BulkUploadItem[], bulkDefaults: BulkUploadDefaults, generate: boolean) => {
    setUploading(true);
    setUploadError(null);
    try {
      const listings = await createBulkPhotoListings(items, bulkDefaults, api.conversations);
      const createdIds = listings.map((listing) => listing.convId);
      const errors = listings.flatMap((listing) => listing.errors);
      const totalPhotos = listings.reduce((sum, listing) => sum + listing.count, 0);
      await Promise.all(createdIds.map((id) => (
        queryClient.invalidateQueries({ queryKey: ["photos", id] })
      )));
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      onBulkListingsCreated?.(listings, generate);
      const title = `Started ${createdIds.length} listings`;
      const description = `Added ${totalPhotos} photo${totalPhotos === 1 ? "" : "s"} from separate folders.`;
      if (errors.length) {
        setUploadError(errors.join("; "));
        toastManager.add({ type: "error", title, description: errors.join("; ") });
      } else {
        toastManager.add({ type: "success", title, description });
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const doUpload = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList);
    if (!files.length) return;
    const groups = groupImageFilesByFolder(files);
    if (groups.length > 1) {
      // The dialog decides; the inputs reset now so picking the same folder again still fires.
      setPendingBulkGroups(groups);
      resetInputs();
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      const only = groups[0]?.files || files;
      const result = await api.conversations.uploadPhotos(convId, only);
      await queryClient.invalidateQueries({ queryKey: ["photos", convId] });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      const errors = result.errors || [];
      if (errors.length) {
        setUploadError(errors.join("; "));
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      resetInputs();
    }
  };

  return (
    <InspectorSection
      data-photo-tray=""
      title="Photos"
      meta={(() => {
        const count = photos?.length || 0;
        if (count === 0) return touchScreen ? undefined : "Drop photos anywhere";
        return `${count} ${count === 1 ? "photo" : "photos"}`;
      })()}
      actions={
        <>
          <Button variant="outline" size="xs" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
            <ImagePlusIcon />
            {uploading ? "Uploading…" : "Add Photos"}
          </Button>
          <Button
            variant="outline"
            size="xs"
            onClick={() => folderInputRef.current?.click()}
            disabled={uploading}
            title="Choose a folder of photos, or a parent folder of item folders for bulk drafts"
          >
            <FolderPlusIcon />
            Add Folder
          </Button>
          <input ref={fileInputRef} type="file" multiple accept="image/*" hidden onChange={(e) => { if (e.target.files) void doUpload(e.target.files); }} />
          <input
            ref={folderInputRef}
            type="file"
            multiple
            accept="image/*"
            hidden
            // Chromium/WebKit folder pick; React has no typed prop for these.
            {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)}
            onChange={(e) => { if (e.target.files) void doUpload(e.target.files); }}
          />
        </>
      }
    >
      {uploadError && <div className="text-destructive-foreground text-xs">{uploadError}</div>}
      {photos && photos.length > 0 && (
        <PhotoStrip>
          {photos.map((p, i) => (
            <PhotoThumb
              key={p.id}
              url={p.url}
              index={i}
              reorderable={canReorder}
              dragging={dragId === p.id}
              dropTarget={dropId === p.id}
              draggable={canReorder}
              onDragStart={(event) => onThumbDragStart(event, p.id)}
              onDragOver={(event) => onThumbDragOver(event, p.id)}
              onDrop={(event) => onThumbDrop(event, p.id)}
              onDragEnd={resetDrag}
              title={canReorder ? "Drag to set upload order" : undefined}
              onOpen={() => openPreview(p.id)}
              onRotate={() => rotate(p.id, 90)}
              rotating={rotatingId === p.id}
              onRemove={() => deleteMutation.mutate(p.id)}
              controlsInViewerOnTouch
            />
          ))}
        </PhotoStrip>
      )}
      {previewPhoto && photos ? (
        <PhotoLightbox
          urls={photos.map((photo) => photo.url)}
          index={previewIndex}
          rotating={rotateMutation.isPending}
          onRotate={(degrees) => rotate(previewPhoto.id, degrees)}
          onStep={(next) => setPreviewId(photos[next].id)}
          onMove={(delta) => {
            const next = moveItem(photos, previewIndex, previewIndex + delta);
            if (next === photos) return;
            setUploadError(null);
            reorderMutation.mutate(next.map((photo) => photo.id));
          }}
          onRemove={() => {
            const rest = photos.filter((photo) => photo.id !== previewPhoto.id);
            deleteMutation.mutate(previewPhoto.id);
            setPreviewId(rest.length ? rest[Math.min(previewIndex, rest.length - 1)].id : null);
          }}
          onClose={() => setPreviewId(null)}
        />
      ) : null}
      {pendingBulkGroups ? (
        <BulkUploadDialog
          groups={pendingBulkGroups}
          onCancel={() => setPendingBulkGroups(null)}
          onConfirm={(bulkDefaults, items, generate) => {
            setPendingBulkGroups(null);
            void doBulkUpload(items, bulkDefaults, generate);
          }}
        />
      ) : null}
    </InspectorSection>
  );
}
