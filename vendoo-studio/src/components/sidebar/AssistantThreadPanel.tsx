import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlusIcon, SearchIcon, Trash2Icon, XIcon } from "lucide-react";
import { api } from "../../api/client";
import type { AssistantThread } from "../../api/assistant";
import { confirmDialog } from "../../ui/confirmDialog";
import { cn } from "../../lib/utils";
import { SIDEBAR_ROW } from "./sidebarStyles";

const SEARCH_CLASS =
  "flex h-8 min-w-0 flex-1 cursor-text items-center gap-2 rounded-[var(--control-radius)] px-2 text-sidebar-muted-foreground transition-colors hover:bg-sidebar-row-hover focus-within:bg-sidebar-row-hover";
const SEARCH_INPUT_CLASS =
  "min-w-0 flex-1 bg-transparent text-sm font-medium text-sidebar-foreground outline-none placeholder:text-sidebar-muted-foreground max-[900px]:text-base";

/**
 * The assistant's conversations, in a right-hand panel on T3 Code's sidebar
 * surface. Listing chats are not in this list; the listings stay on the left.
 * On mobile it is a sheet that slides in from the right.
 */
export function AssistantThreadPanel({
  selectedId,
  onSelect,
  mobileOpen,
  onCloseMobile,
}: {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** `undefined` on desktop, where the panel is always shown. */
  mobileOpen?: boolean;
  onCloseMobile: () => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const threads = useQuery({ queryKey: ["assistant", "threads"], queryFn: api.assistant.threads });
  const remove = useMutation({
    mutationFn: (id: string) => api.assistant.deleteThread(id),
    onSuccess: (_result, id) => {
      queryClient.setQueryData<AssistantThread[]>(["assistant", "threads"], (saved) => saved?.filter((thread) => thread.id !== id));
      queryClient.removeQueries({ queryKey: ["assistant", "messages", id] });
      if (selectedId === id) onSelect(null);
    },
  });
  const needle = query.trim().toLowerCase();
  const shown = (threads.data ?? []).filter((thread) => !needle || thread.title.toLowerCase().includes(needle));

  const deleteThread = async (thread: AssistantThread) => {
    const confirmed = await confirmDialog(`Delete “${thread.title}”? The messages in it are removed. Listings are not.`, {
      variant: "destructive",
      confirmLabel: "Delete thread",
    });
    if (!confirmed) return;
    remove.mutate(thread.id);
  };

  const pick = (id: string | null) => {
    onSelect(id);
    onCloseMobile();
  };

  return (
    <aside
      id="assistant-threads"
      data-app-sidebar
      className={cn("panel thread-sidebar", mobileOpen && "is-mobile-open")}
      aria-label="Assistant chats"
      aria-hidden={mobileOpen === false ? true : undefined}
    >
      <div className="hidden h-14 shrink-0 items-center justify-between px-4 pt-2 max-[900px]:flex">
        <span className="text-lg font-semibold text-sidebar-foreground">Chats</span>
        <button
          type="button"
          className="inline-flex size-9 cursor-pointer items-center justify-center rounded-full text-sidebar-muted-foreground hover:bg-sidebar-row-hover hover:text-sidebar-foreground"
          aria-label="Hide chats"
          onClick={onCloseMobile}
        >
          <XIcon className="size-4" />
        </button>
      </div>
      <div className="flex shrink-0 items-center gap-1 px-(--sidebar-content-inset) py-2">
        <label className={SEARCH_CLASS}>
          <SearchIcon className="size-4 shrink-0 text-(--sidebar-icon-color)" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className={SEARCH_INPUT_CLASS}
            placeholder="Search chats"
            aria-label="Search chats"
          />
        </label>
        <button
          type="button"
          className={cn(SIDEBAR_ROW, "w-auto shrink-0 px-2 text-xs font-semibold")}
          aria-pressed={selectedId === null}
          onClick={() => pick(null)}
        >
          <PlusIcon />
          New chat
        </button>
      </div>
      <div
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-(--sidebar-content-inset) pb-2 max-[900px]:touch-pan-y max-[900px]:pb-[calc(16px+env(safe-area-inset-bottom,0px))]"
        aria-label="Assistant threads"
      >
        {threads.isPending ? (
          <p className="px-2.5 py-6 text-center text-xs text-sidebar-muted-foreground">Loading chats</p>
        ) : threads.isError ? (
          <p className="px-2.5 py-6 text-center text-xs text-sidebar-muted-foreground">Could not load chats</p>
        ) : shown.length === 0 ? (
          <p className="px-2.5 py-6 text-center text-xs text-sidebar-muted-foreground">
            {threads.data?.length ? "No matching chats" : "No chats yet"}
          </p>
        ) : (
          shown.map((thread) => (
            <div key={thread.id} className="group flex items-center gap-0.5">
              <button
                type="button"
                className={cn(SIDEBAR_ROW, "min-w-0 flex-1")}
                data-active={selectedId === thread.id}
                aria-current={selectedId === thread.id ? "true" : undefined}
                onClick={() => pick(thread.id)}
              >
                <span className="truncate">{thread.title}</span>
              </button>
              <button
                type="button"
                className="inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-[var(--control-radius)] text-sidebar-muted-foreground opacity-0 transition-opacity hover:bg-sidebar-row-hover hover:text-sidebar-foreground focus-visible:opacity-100 group-hover:opacity-100 max-[900px]:opacity-100"
                aria-label={`Delete ${thread.title}`}
                disabled={remove.isPending}
                onClick={() => { void deleteThread(thread); }}
              >
                <Trash2Icon className="size-3.5" />
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
