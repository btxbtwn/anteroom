import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ASSISTANT_MEMORY_KEY, ASSISTANT_NAME_KEY, ASSISTANT_NOTE_KEY } from "../../api/assistant";
import { confirmDialog } from "../../ui/confirmDialog";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

const NOTE_MAX = 4000;
const NAME_MAX = 40;

/** What the seller wants Anteroom to call them; the Assistant home greets them by it. */
function NameSection() {
  const queryClient = useQueryClient();
  const saved = useQuery({ queryKey: ASSISTANT_NAME_KEY, queryFn: api.assistant.name });
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (name: string) => api.assistant.saveName(name),
    onSuccess: (result) => {
      queryClient.setQueryData(ASSISTANT_NAME_KEY, result);
      setDraft(null);
    },
  });
  const name = draft ?? saved.data?.name ?? "";
  const changed = draft !== null && draft.trim() !== (saved.data?.name ?? "");

  return (
    <SettingsSection title="Your name">
      <SettingsRow
        id="preferred-name"
        title="What should Anteroom call you?"
        description="The Assistant greets you by it. Leave it empty for a plain greeting."
      >
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate(name);
          }}
        >
          <Input
            size="sm"
            className="w-56 max-w-full"
            aria-label="Your name"
            autoComplete="given-name"
            maxLength={NAME_MAX}
            value={name}
            disabled={saved.isLoading}
            placeholder="Your first name"
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button type="submit" size="sm" disabled={!changed || save.isPending || saved.isLoading}>Save</Button>
        </form>
        {saved.isError ? <SettingsNote tone="error">Could not load your name.</SettingsNote> : null}
        {save.isError ? <SettingsNote tone="error">{(save.error as Error).message || "Could not save."}</SettingsNote> : null}
      </SettingsRow>
    </SettingsSection>
  );
}

/** What the seller tells the business assistant to keep in mind on every question. */
function BusinessNoteSection() {
  const queryClient = useQueryClient();
  const saved = useQuery({ queryKey: ASSISTANT_NOTE_KEY, queryFn: api.assistant.note });
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (note: string) => api.assistant.saveNote(note),
    onSuccess: (result) => {
      queryClient.setQueryData(ASSISTANT_NOTE_KEY, result);
      setDraft(null);
    },
  });
  const note = draft ?? saved.data?.note ?? "";
  const changed = draft !== null && draft !== (saved.data?.note ?? "");

  return (
    <SettingsSection title="About your business">
      <SettingsRow
        id="business-note"
        title="What the assistant keeps in mind"
        description="Your goals, margin, time, what you won’t source and where you ship from; the assistant reads it on every question."
      >
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate(note);
          }}
        >
          <Textarea
            id="assistant-note-text"
            aria-label="About your business"
            rows={5}
            maxLength={NOTE_MAX}
            value={note}
            disabled={saved.isLoading}
            placeholder="I want at least $15 profit per item and sell mostly vintage menswear. I list about 20 items a week."
            onChange={(event) => setDraft(event.target.value)}
          />
          {saved.isError ? <SettingsNote tone="error">Could not load what you saved.</SettingsNote> : null}
          {save.isError ? (
            <SettingsNote tone="error">{(save.error as Error).message || "Could not save."}</SettingsNote>
          ) : null}
          <div className="flex justify-end gap-2">
            {changed ? (
              <Button type="button" size="sm" variant="ghost" onClick={() => setDraft(null)}>Discard</Button>
            ) : null}
            <Button type="submit" size="sm" disabled={!changed || save.isPending || saved.isLoading}>Save</Button>
          </div>
        </form>
      </SettingsRow>
    </SettingsSection>
  );
}

/** Facts the assistant picked up in chat. They outlive any one conversation. */
function MemorySection() {
  const queryClient = useQueryClient();
  const remembered = useQuery({ queryKey: ASSISTANT_MEMORY_KEY, queryFn: api.assistant.memory });
  const forget = useMutation({
    mutationFn: (id: string) => api.assistant.forget(id),
    onSuccess: (result) => queryClient.setQueryData(ASSISTANT_MEMORY_KEY, result),
  });
  const memories = remembered.data?.memories ?? [];

  return (
    <SettingsSection title="What it remembers">
      <SettingsRow
        id="assistant-memory"
        title="Facts from your chats"
        description="They carry into new chats and update when you correct the assistant; remove any that are wrong."
        status={
          remembered.isError
            ? <SettingsNote tone="error">Could not load what it remembers.</SettingsNote>
            : !memories.length
              ? (remembered.isLoading ? "Loading…" : "Nothing yet. Mention how you like to run the shop and it will keep it.")
              : null
        }
      />
      {memories.map((fact) => (
        <SettingsRow
          key={fact.id}
          title={<span className="font-normal">{fact.text}</span>}
          control={
            <Button
              type="button"
              size="xs"
              variant="ghost-muted"
              disabled={forget.isPending}
              onClick={async () => {
                const confirmed = await confirmDialog(
                  `Forget this memory?\nThe assistant will stop keeping “${fact.text}” in mind for future chats.`,
                  { variant: "destructive", confirmLabel: "Remove" },
                );
                if (confirmed) forget.mutate(fact.id);
              }}
            >
              Remove
            </Button>
          }
        />
      ))}
      {forget.isError ? (
        <SettingsRow
          title={<SettingsNote tone="error">{(forget.error as Error).message || "Could not remove that."}</SettingsNote>}
        />
      ) : null}
    </SettingsSection>
  );
}

export function AssistantSettings() {
  return (
    <>
      <NameSection />
      <BusinessNoteSection />
      <MemorySection />
    </>
  );
}
