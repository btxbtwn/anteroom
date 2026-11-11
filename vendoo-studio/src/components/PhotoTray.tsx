import { useWorkspaceRuntime } from "../app/runtime";
import React, { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { groupImageFilesByFolder, moveItem, type PhotoFolderGroup } from "../photoDrop";
import type { ListingPhoto } from "../api/types";
import { toastManager } from "./ui/toast";
import { BulkUploadDialog } from "./BulkUploadDialog";
import { InspectorSection } from "./ListingFormControls";
import { PhotoEditor } from "./PhotoEditor";
import { PhotoLightbox, PhotoStrip, PhotoThumb } from "./PhotoThumbs";
import { SortablePhoto, SortablePhotos } from "./SortablePhotos";
import { useListingSessionActive } from "./listingSession";
import { DRAFT_ONLY_PHOTOS, useCanAddPhotos } from "./photoAdding";
import { photoThumbUrl } from "../photoThumb";
import { Button } from "./ui/button";
import { FolderPlusIcon, ImagePlusIcon } from "lucide-react";
import {
  createBulkPhotoListings,
  type BulkListingUploadResult,
  type BulkUploadDefaults,
  type BulkUploadItem,
} from "../bulkPhotoUpload";

interface Props {
  convId: string;
  /** When a folder pick expands into several item folders, App can focus the first draft. */
  onBulkListingsCreated?: (listings: BulkListingUploadResult[], generate: boolean) => void;
}

export function PhotoTray({ convId, onBulkListingsCreated }: Props) {
  const runtime = useWorkspaceRuntime();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [pendingBulkGroups, setPendingBulkGroups] = useState<PhotoFolderGroup[] | null>(null);

  const { data: photos } = useQuery({
    queryKey: ["photos", convId],
    queryFn: ({ signal }) => api.conversations.photos(convId, undefined, { signal }),
  });

  const deleteMutation = useMutation({
    mutationFn: (photoId: string) => api.conversations.deletePhoto(convId, photoId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["photos", convId] });
      // The sidebar thumbnail comes from the conversation list's cover photo.
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  // A rotation or edit keeps the photo's ID and serves a new file under a new URL.
  const replacePhoto = (changed: ListingPhoto) => {
    queryClient.setQueryData<ListingPhoto[]>(["photos", convId], (current) =>
      current?.map((photo) => (photo.id === changed.id ? changed : photo)),
    );
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
  };

  const rotateMutation = useMutation({
    mutationFn: ({ photoId, degrees }: { photoId: string; degrees: 90 | -90 }) =>
      api.conversations.rotatePhoto(convId, photoId, degrees),
    onSuccess: replacePhoto,
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
      const previous = queryClient.getQueryData<ListingPhoto[]>(["photos", convId]);
      if (previous) {
        const byId = new Map(previous.map((photo) => [photo.id, photo]));
        queryClient.setQueryData(
          ["photos", convId],
          orderedIds.map((id) => byId.get(id)).filter((photo): photo is ListingPhoto => Boolean(photo)),
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
  const editingIndex = photos?.findIndex((p) => p.id === editingId) ?? -1;
  const editingPhoto = photos && editingIndex >= 0 ? photos[editingIndex] : null;
  const previewPhoto = photos && previewIndex >= 0 ? photos[previewIndex] : null;

  useEffect(() => {
    setPreviewId(null);
    setEditingId(null);
  }, [convId]);

  useEffect(() => {
    if (previewId && photos && !photos.some((p) => p.id === previewId)) {
      setPreviewId(null);
    }
  }, [photos, previewId]);

  // A hidden listing tab stays mounted. Its photo tiles must not: each one
  // would keep a full-size decoded bitmap.
  const sessionActive = useListingSessionActive();
  const canAdd = useCanAddPhotos(convId);

  const canReorder = (photos?.length || 0) > 1;
  // A phone cannot drop files.
  const touchScreen = typeof window !== "undefined" && Boolean(window.matchMedia?.("(hover: none)").matches);

  const movePhoto = (from: number, to: number) => {
    if (!photos) return;
    const next = moveItem(photos, from, to);
    if (next === photos) return;
    setUploadError(null);
    reorderMutation.mutate(next.map((photo) => photo.id));
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

  if (!sessionActive) return null;

  return (
    <InspectorSection
      data-photo-tray=""
      title="Photos"
      meta={(() => {
        const count = photos?.length || 0;
        if (count === 0) return touchScreen || !canAdd ? undefined : "Drop photos anywhere";
        return `${count} ${count === 1 ? "photo" : "photos"}`;
      })()}
      actions={canAdd ? (
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
      ) : undefined}
    >
      {!canAdd && <p className="text-xs text-muted-foreground">{DRAFT_ONLY_PHOTOS}</p>}
      {uploadError && <div className="text-destructive-foreground text-xs">{uploadError}</div>}
      {photos && photos.length > 0 && (
        <PhotoStrip className="gap-2">
          <SortablePhotos ids={photos.map((p) => p.id)} onMove={movePhoto}>
            {photos.map((p, i) => (
              <SortablePhoto key={p.id} id={p.id} disabled={!canReorder}>
                {(dragging) => (
                  <PhotoThumb
                    className="size-30 sm:size-34"
                    url={photoThumbUrl(p)}
                    index={i}
                    reorderable={canReorder}
                    dragging={dragging}
                    title={canReorder ? (touchScreen ? "Press and hold, then drag to set upload order" : "Drag to set upload order") : undefined}
                    onOpen={() => setPreviewId(p.id)}
                    onRotate={runtime.features.has("photo_edits") ? () => rotate(p.id, 90) : undefined}
                    rotating={rotatingId === p.id}
                    onRemove={() => deleteMutation.mutate(p.id)}
                    controlsInViewerOnTouch
                  />
                )}
              </SortablePhoto>
            ))}
          </SortablePhotos>
        </PhotoStrip>
      )}
      {previewPhoto && photos ? (
        <PhotoLightbox
          urls={photos.map((photo) => photo.url)}
          index={previewIndex}
          rotating={rotateMutation.isPending}
          onRotate={runtime.features.has("photo_edits") ? (degrees) => rotate(previewPhoto.id, degrees) : undefined}
          edited={Boolean(previewPhoto.edits)}
          onEdit={runtime.features.has("photo_edits") ? () => {
            setEditingId(previewPhoto.id);
            setPreviewId(null);
          } : undefined}
          onStep={(next) => setPreviewId(photos[next].id)}
          onMove={(delta) => movePhoto(previewIndex, previewIndex + delta)}
          onRemove={() => {
            const rest = photos.filter((photo) => photo.id !== previewPhoto.id);
            deleteMutation.mutate(previewPhoto.id);
            setPreviewId(rest.length ? rest[Math.min(previewIndex, rest.length - 1)].id : null);
          }}
          onClose={() => setPreviewId(null)}
        />
      ) : null}
      {editingPhoto ? (
        <PhotoEditor
          key={editingPhoto.id}
          convId={convId}
          photo={editingPhoto}
          index={editingIndex}
          onSaved={replacePhoto}
          onClose={() => {
            // Back to the enlarged photo the editor was opened from.
            setPreviewId(editingPhoto.id);
            setEditingId(null);
          }}
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
