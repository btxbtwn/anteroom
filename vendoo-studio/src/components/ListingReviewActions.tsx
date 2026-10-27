import { useQuery } from "@tanstack/react-query";
import { EllipsisIcon, Globe2Icon } from "lucide-react";
import type { MouseEvent, ReactNode } from "react";
import { api } from "../api/client";
import { jobsPollMs } from "../api/polling";
import { useListingSessionActive } from "./listingSession";
import { cn } from "../lib/utils";
import { RegenerateListingButton, useClearListing } from "./ClearListingButton";
import { OpenListingButton } from "./OpenListingButton";
import { Button } from "./ui/button";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "./ui/menu";
import { Tooltip, TooltipPopup, TooltipTrigger } from "./ui/tooltip";

function notesVendooItemId(notes?: string | null): string | null {
  if (!notes) return null;
  try {
    const parsed = JSON.parse(notes);
    const itemId = String(parsed?.vendooItemId || "").trim();
    return itemId || null;
  } catch {
    return null;
  }
}

function notesVendooUrl(notes?: string | null): string | null {
  if (!notes) return null;
  try {
    const parsed = JSON.parse(notes);
    const url = String(parsed?.vendooUrl || "").trim();
    return url || null;
  } catch {
    return null;
  }
}

/** The listing's own job, plus the Vendoo ids the notes remember after a clear. */
function useListingJob(convId: string) {
  const sessionActive = useListingSessionActive();
  const { data: jobs } = useQuery({
    queryKey: ["jobs", convId],
    queryFn: () => api.jobs.list(convId),
    refetchInterval: (query) => (sessionActive ? jobsPollMs(query.state.data) : false),
  });
  const { data: conversation } = useQuery({
    queryKey: ["conversation", convId],
    queryFn: () => api.conversations.get(convId),
  });
  const listingJob = jobs?.find((job) => job.conversation_id === convId && job.status !== "cancelled" && job.mode !== "native_marketplace");
  return {
    listingJob,
    importedItemId: listingJob?.vendoo_item_id || notesVendooItemId(conversation?.notes),
    importedUrl: listingJob?.vendoo_url || notesVendooUrl(conversation?.notes),
  };
}


/** Icon-only: it opens a surface in the window the way T3 Code's panel toggles
    do (lucide globe-2, as in T3's RightPanelTabs), not a listing action. */
export function ListingBrowserButton({
  convId,
  browserOpen,
  onOpenBrowser,
}: {
  convId: string;
  browserOpen?: boolean;
  onOpenBrowser?: (jobId: string) => void;
}) {
  const { listingJob } = useListingJob(convId);
  if (!listingJob || !onOpenBrowser) return null;
  if (!listingJob.vendoo_item_id && !listingJob.vendoo_url) return null;

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-xs"
            disabled={browserOpen}
            aria-label="Open the Vendoo draft in Anteroom"
            aria-pressed={browserOpen}
            data-pressed={browserOpen ? "" : undefined}
            onClick={() => onOpenBrowser(listingJob.id)}
          />
        }
      >
        <Globe2Icon />
      </TooltipTrigger>
      <TooltipPopup side="bottom">
        Work in the Vendoo draft here: click, type, and point Anteroom at fields to fill
      </TooltipPopup>
    </Tooltip>
  );
}

/** Everything rare enough not to earn a place in the header. Clearing is the
    only item today, and it stays out of reach of the tabs next door. */
function ListingActionsMenu({ convId, onCleared }: { convId: string; onCleared?: () => void }) {
  const { clear, isPending } = useClearListing(convId, onCleared);

  return (
    <Menu>
      <MenuTrigger
        render={<Button variant="ghost" size="icon-xs" aria-label="More listing actions" />}
      >
        <EllipsisIcon />
      </MenuTrigger>
      <MenuPopup align="end" width="content">
        <MenuItem variant="destructive" disabled={isPending} onClick={() => void clear()}>
          {isPending ? "Clearing…" : "Clear listing"}
        </MenuItem>
      </MenuPopup>
    </Menu>
  );
}

/** Open listing / Regenerate, then the icon controls (browser, More) — header
    actions for a listing. */
export function ListingReviewActions({
  convId,
  onCleared,
  className,
  onMouseDown,
  children,
}: {
  convId: string;
  onCleared?: () => void;
  className?: string;
  onMouseDown?: (event: MouseEvent<HTMLElement>) => void;
  /** The browser toggle, which sits with this listing's actions. */
  children?: ReactNode;
}) {
  const { listingJob, importedItemId, importedUrl } = useListingJob(convId);

  return (
    <div className={cn("ms-auto flex shrink-0 items-center gap-1", className)} onMouseDown={onMouseDown}>
      {listingJob ? (
        <OpenListingButton
          jobId={listingJob.id}
          vendooItemId={listingJob.vendoo_item_id || importedItemId}
          vendooUrl={listingJob.vendoo_url || importedUrl}
        />
      ) : null}
      <RegenerateListingButton convId={convId} onRegenerated={onCleared} />
      {children}
      <ListingActionsMenu convId={convId} onCleared={onCleared} />
    </div>
  );
}
