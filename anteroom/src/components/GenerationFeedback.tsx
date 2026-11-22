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
  const [editing, setEditing] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: (rating: GenerationRating) => api.generationFeedback.save(id, current.data!.revision_id, rating),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["generation-feedback"] });
      setEditing(false);
    },
  });
  if (!current.data) return null;
  const saved = current.data.feedback;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-muted-foreground">Rate AI generation</span>
      <Button variant="ghost" aria-label="Thumbs up" aria-pressed={saved?.rating === "up"} disabled={save.isPending}
        onClick={() => save.mutate({ rating: "up", issues: [], note: "" })}><ThumbsUpIcon /></Button>
      <Button variant="ghost" aria-label="Thumbs down" aria-pressed={saved?.rating === "down"} disabled={save.isPending}
        onClick={() => { save.reset(); setTags(saved?.issues ?? []); setNote(saved?.note ?? ""); setEditing(true); }}><ThumbsDownIcon /></Button>
      {save.error && !editing && <p role="alert">{save.error.message}</p>}
      <Dialog open={editing} onOpenChange={setEditing}>
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
            <Button disabled={save.isPending} onClick={() => save.mutate({ rating: "down", issues: tags, note })}>Save feedback</Button>
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
