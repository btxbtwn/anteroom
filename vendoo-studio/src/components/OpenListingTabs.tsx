import { useEffect, useRef } from "react";
import { PlusIcon, XIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { stopTitlebarDrag } from "./WorkspaceTopbar";

interface Props {
  tabs: { id: string; title: string }[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onCreate: () => void;
  creating: boolean;
}

export function OpenListingTabs({ tabs, selectedId, onSelect, onClose, onCreate, creating }: Props) {
  const createButton = useRef<HTMLButtonElement>(null);
  const focusTab = (id: string) => {
    const button = document.getElementById(`listing-tab-${id}`);
    button?.focus();
  };
  useEffect(() => {
    const button = selectedId ? document.getElementById(`listing-tab-${selectedId}`) : null;
    button?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [selectedId]);

  const closeTab = (id: string) => {
    const index = tabs.findIndex((tab) => tab.id === id);
    const remaining = tabs.filter((tab) => tab.id !== id);
    onClose(id);
    const nextId = selectedId === id
      ? remaining[Math.min(index, remaining.length - 1)]?.id
      : selectedId;
    if (nextId) focusTab(nextId);
    else createButton.current?.focus();
  };

  /* Pill tabs after T3 Code's RightPanelTabs (MIT, (c) 2026 T3 Tools Inc.). */
  return (
    // Lives in the window titlebar, which phones don't show: they open one listing at a time.
    <div className="pointer-events-auto flex min-w-0 items-center gap-1 [-webkit-app-region:no-drag]" onMouseDown={stopTitlebarDrag}>
      <div
        className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        role={tabs.length ? "tablist" : undefined}
        aria-label="Open listings"
      >
        {tabs.map((tab, index) => {
          const active = selectedId === tab.id;
          return (
            <div
              key={tab.id}
              role="presentation"
              data-active={active}
              className={cn(
                "group/tab flex h-6 max-w-60 shrink-0 items-center gap-0.5 rounded-md pr-0.5 pl-2 text-xs",
                active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )}
            >
              <button
                id={`listing-tab-${tab.id}`}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls="listing-workspace-panel"
                tabIndex={active ? 0 : -1}
                className="min-w-0 flex-1 cursor-pointer truncate rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
                title={tab.title}
                onClick={() => onSelect(tab.id)}
                onKeyDown={(event) => {
                  let nextIndex: number;
                  if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
                  else if (event.key === "ArrowLeft") nextIndex = (index + tabs.length - 1) % tabs.length;
                  else if (event.key === "Home") nextIndex = 0;
                  else if (event.key === "End") nextIndex = tabs.length - 1;
                  else if (event.key === "Delete") {
                    event.preventDefault();
                    closeTab(tab.id);
                    return;
                  } else return;
                  event.preventDefault();
                  onSelect(tabs[nextIndex].id);
                  focusTab(tabs[nextIndex].id);
                }}
              >
                {tab.title}
              </button>
              <button
                type="button"
                className="flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-sm opacity-0 outline-none group-hover/tab:opacity-100 group-data-[active=true]/tab:opacity-100 hover:bg-muted focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={`Close tab: ${tab.title}`}
                title="Close tab"
                tabIndex={active ? 0 : -1}
                onClick={() => closeTab(tab.id)}
              >
                <XIcon className="size-3" />
              </button>
            </div>
          );
        })}
      </div>
      <Button
        ref={createButton}
        type="button"
        variant="ghost"
        size="icon-xs"
        aria-label="New listing"
        title="New listing"
        disabled={creating}
        onClick={onCreate}
      >
        <PlusIcon />
      </Button>
    </div>
  );
}
