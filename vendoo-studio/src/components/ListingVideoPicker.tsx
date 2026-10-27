import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { videoProblem } from "../api/listingVideos";
import { Button } from "./ui/button";

export function ListingVideoPicker({ convId, marketplace, selectedId, disabled, onSelect }: {
  convId: string;
  marketplace: string;
  selectedId: string;
  disabled?: boolean;
  onSelect: (id: string) => void;
}) {
  const input = React.useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const key = ["listing-videos", convId];
  const library = useQuery({ queryKey: key, queryFn: () => api.listingVideos.list(convId) });
  const upload = useMutation({ mutationFn: (file: File) => api.listingVideos.upload(convId, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }) });
  const remove = useMutation({ mutationFn: (id: string) => api.listingVideos.delete(convId, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }) });
  const error = upload.error || remove.error || library.error;
  const rules = library.data?.rules?.[marketplace];
  return <div className="space-y-3">
    <input ref={input} className="sr-only" type="file" accept="video/*" aria-label="Upload listing video" disabled={disabled || upload.isPending}
      onChange={event => { const file = event.target.files?.[0]; if (file) upload.mutate(file); event.target.value = ""; }} />
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="outline" size="sm" disabled={disabled || upload.isPending} onClick={() => input.current?.click()}>
        {upload.isPending ? "Uploading video…" : "Upload video"}
      </Button>
      {selectedId && <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={() => onSelect("")}>Clear video</Button>}
    </div>
    {error && <p role="alert" className="text-xs text-destructive">{error instanceof Error ? error.message : "The videos could not be loaded."}</p>}
    {selectedId && library.data && !library.data.videos.some(video => video.id === selectedId)
      && <p role="alert" className="text-xs text-destructive">The selected video is missing. Choose another video or clear it.</p>}
    {library.data?.videos.map(video => {
      const problem = videoProblem(video, rules);
      return <div key={video.id} className="min-w-0 space-y-2 rounded-lg border p-3">
      <video controls preload="metadata" src={video.url} className="max-h-40 w-full rounded-md" aria-label={video.original_filename} />
      <p className="break-words text-xs">{video.original_filename} · {video.duration_seconds.toFixed(1)} seconds</p>
      {problem && <p className="text-xs text-destructive">{problem}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant={selectedId === video.id ? "secondary" : "outline"} aria-pressed={selectedId === video.id}
          disabled={disabled || !!problem} onClick={() => onSelect(video.id)}>{selectedId === video.id ? "Selected" : "Use this video"}</Button>
        <Button type="button" variant="ghost" size="sm" disabled={disabled || selectedId === video.id || remove.isPending}
          onClick={() => remove.mutate(video.id)}>Delete video</Button>
      </div>
    </div>;
    })}
    {library.isLoading && <p className="text-xs text-muted-foreground">Loading videos…</p>}
  </div>;
}
