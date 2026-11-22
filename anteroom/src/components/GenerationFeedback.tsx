import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { api, type GenerationRating } from "../api/client";
import { Button } from "./ui/button";
import { Dialog, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "./ui/dialog";
import { Textarea } from "./ui/textarea";

const issues = {
  category: "Wrong category",
  price: "Bad price or comps",
  flaw: "Missed flaw",
  size: "Wrong size",
  style: "Title or description style",
};

export function GenerationFeedback({ id, revision }: { id: string; revision: string }) {
  const client = useQueryClient();
  const queryKey = ["generation-feedback", id, revision];
  const current = useQuery({ queryKey, queryFn: () => api.generationFeedback.get(id) });
  const [targetRevision, setTargetRevision] = useState<string | null>(null);
  const editing = targetRevision !== null;
  const [tags, setTags] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: ({ revisionId, rating }: { revisionId: string; rating: GenerationRating }) =>
      api.generationFeedback.save(id, revisionId, rating),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["generation-feedback"] });
      setTargetRevision(null);
    },
  });
  if (!current.data && !editing && !current.isError) return null;
  const revisionId = current.data?.revision_id;
  const saved = current.data?.feedback;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {current.isError && (
        <div className="flex items-center gap-2">
          <p role="alert">{current.error.message}</p>
          <Button variant="outline" disabled={current.isFetching} onClick={() => void current.refetch()}>Retry</Button>
        </div>
      )}
      {revisionId && <>
      <span className="text-sm text-muted-foreground">Rate AI generation</span>
      <Button variant="ghost" aria-label="Thumbs up" aria-pressed={saved?.rating === "up"} disabled={save.isPending}
        onClick={() => save.mutate({ revisionId, rating: { rating: "up", issues: [], note: "" } })}><ThumbsUpIcon /></Button>
      <Button variant="ghost" aria-label="Thumbs down" aria-pressed={saved?.rating === "down"} disabled={save.isPending}
        onClick={() => { save.reset(); setTags(saved?.issues ?? []); setNote(saved?.note ?? ""); setTargetRevision(revisionId); }}><ThumbsDownIcon /></Button>
      </>}
      {save.error && !editing && <p role="alert">{save.error.message}</p>}
      <Dialog open={editing} onOpenChange={(open) => { if (!open) setTargetRevision(null); }}>
        <DialogPopup>
          <DialogHeader><DialogTitle>What needs improvement?</DialogTitle></DialogHeader>
          <DialogPanel>
            <div className="flex flex-wrap gap-2">
            {Object.entries(issues).map(([tag, label]) => (
              <Button key={tag} variant={tags.includes(tag) ? "secondary" : "outline"} aria-pressed={tags.includes(tag)}
                onClick={() => setTags((previous) => previous.includes(tag) ? previous.filter((item) => item !== tag) : [...previous, tag])}>
                {label}
              </Button>
            ))}
            </div>
            <Textarea aria-label="Optional feedback note" placeholder="Optional note" maxLength={500} value={note}
              onChange={(event) => setNote(event.target.value)} />
            {save.error && <p role="alert">{save.error.message}</p>}
          </DialogPanel>
          <DialogFooter>
            <Button disabled={save.isPending || !targetRevision} onClick={() => {
              if (targetRevision) save.mutate({ revisionId: targetRevision, rating: { rating: "down", issues: tags, note } });
            }}>Save feedback</Button>
          </DialogFooter>
        </DialogPopup>
      </Dialog>
    </div>
  );
}

export function GenerationFeedbackSummary() {
  const query = useQuery({ queryKey: ["generation-feedback", "summary"], queryFn: api.generationFeedback.summary });
  return (
    <section className="space-y-2" aria-label="AI generation feedback">
      <h3>AI generation feedback · last 30 days</h3>
      {query.data ? (
        <>
          <p>{query.data.up} thumbs up · {query.data.down} thumbs down</p>
          {Object.entries(issues).map(([tag, label]) => query.data.issues[tag]
            ? <p key={tag}>{label} · {query.data.issues[tag]}</p> : null)}
        </>
      ) : <p role="status">{query.error?.message || "Loading feedback…"}</p>}
    </section>
  );
}
