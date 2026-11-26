import { lazy, memo, Suspense, useCallback, useEffect, useState, type ReactNode } from "react";
import type { BulkListingUploadResult } from "../bulkPhotoUpload";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { isChatToggleShortcut, ListingChatPopup, type ListingChatStatus } from "../components/ListingChatPopup";
import { useListingSessionActive } from "../components/listingSession";
import { isConfirmDialogOpen } from "../ui/confirmDialog";
import type { ListingReviewTab } from "../components/ListingReviewTabs";
import { Spinner } from "../components/ui/spinner";
import type { OpenListing, OpenListingAction } from "./openListing";
import { loadChatPanel, loadListingEditor } from "./pageLoaders";

const ChatPanel = lazy(loadChatPanel);
const ListingEditor = lazy(loadListingEditor);

export const pageLoading = (
  <div className="flex flex-1 items-center justify-center" role="status" aria-label="Loading">
    <Spinner className="size-5 text-muted-foreground" />
  </div>
);

/**
 * What the open listing's panes ask of the workspace. App passes one object
 * for the whole session, so the panes re-render only when their listing does,
 * not on every poll, search keystroke or sidebar change.
 */
export type ListingPaneActions = {
  dispatch: (action: OpenListingAction) => void;
  askChat: (id: string, text: string) => void;
  cleared: (id: string) => void;
  editInventoryLocation: () => void;
  bulkCreated: (listings: BulkListingUploadResult[], generate: boolean) => void;
};

export const ListingChatPane = memo(function ListingChatPane({
  listing,
  actions,
  onStatusChange,
}: {
  listing: OpenListing;
  actions: ListingPaneActions;
  onStatusChange?: (status: ListingChatStatus) => void;
}) {
  const { id } = listing;
  const consumed = useCallback(() => actions.dispatch({ type: "message", id, value: null }), [actions, id]);
  return (
    <ErrorBoundary label="Ant" resetKey={id}>
      <Suspense fallback={pageLoading}>
        <ChatPanel
          convId={id}
          queuedMessage={listing.queuedMessage}
          onQueuedMessageConsumed={consumed}
          onStatusChange={onStatusChange}
        />
      </Suspense>
    </ErrorBoundary>
  );
});

/**
 * Whether Ant's column is open on this listing. `allowed` is the seller's
 * remembered choice: once they minimize Ant it stays minimized everywhere.
 * Otherwise Ant opens on a listing that has a chat, is generating or still
 * needs generating, and stays a launcher on one that doesn't, so the form
 * keeps the room until the seller opens Ant there or sends something to it.
 */
export function listingChatOpen({ allowed, hasChat, openedHere }: { allowed: boolean; hasChat: boolean; openedHere: boolean }): boolean {
  return allowed && (hasChat || openedHere);
}

/** Desktop: the chat beside or over the form. Its status changes as a reply streams, so it lives here, not in App. */
const DesktopListingChat = memo(function DesktopListingChat({
  listing,
  actions,
  open: allowed,
  onOpenChange,
}: {
  listing: OpenListing;
  actions: ListingPaneActions;
  /** False once the seller minimized Ant; remembered across listings. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [status, setStatus] = useState<ListingChatStatus | null>(null);
  // Opened on this listing (the launcher, ⌘J, or a "Fix with Ant" that queued a message).
  const [openedHere, setOpenedHere] = useState(false);
  if (listing.queuedMessage && !openedHere) setOpenedHere(true);
  // Generate lives in the chat, so a draft still waiting for it opens Ant too.
  const hasChat = status?.convId === listing.id && (!status.empty || status.generate);
  const open = listingChatOpen({ allowed, hasChat, openedHere });
  const setOpen = useCallback((next: boolean) => {
    if (next) setOpenedHere(true);
    onOpenChange(next);
  }, [onOpenChange]);
  const active = useListingSessionActive();
  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isChatToggleShortcut(event) || isConfirmDialogOpen()) return;
      event.preventDefault();
      setOpen(!open);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, open, setOpen]);
  return (
    <ListingChatPopup convId={listing.id} open={open} onOpenChange={setOpen} status={status}>
      <ListingChatPane listing={listing} actions={actions} onStatusChange={setStatus} />
    </ListingChatPopup>
  );
});

export const ListingEditorPane = memo(function ListingEditorPane({
  listing,
  actions,
  chatOpen,
  onChatOpenChange,
}: {
  listing: OpenListing;
  actions: ListingPaneActions;
  /** Desktop with generation: whether the chat column is open. Absent on phones, where chat is its own pane. */
  chatOpen?: boolean;
  onChatOpenChange?: (open: boolean) => void;
}) {
  const { id } = listing;
  const onReviewTabChange = useCallback(
    (value: ListingReviewTab) => actions.dispatch({ type: "review", id, value }),
    [actions, id],
  );
  const onEditTabChange = useCallback((value: string) => actions.dispatch({ type: "tab", id, value }), [actions, id]);
  const onAskChat = useCallback((text: string) => actions.askChat(id, text), [actions, id]);
  const onCleared = useCallback(() => actions.cleared(id), [actions, id]);
  let chatNode: ReactNode;
  if (chatOpen !== undefined && onChatOpenChange) {
    chatNode = <DesktopListingChat listing={listing} actions={actions} open={chatOpen} onOpenChange={onChatOpenChange} />;
  }
  return (
    <ErrorBoundary label="This listing" resetKey={id}>
      <Suspense fallback={null}>
        <ListingEditor
          convId={id}
          reviewTab={listing.reviewTab}
          onReviewTabChange={onReviewTabChange}
          editTab={listing.editTab}
          onEditTabChange={onEditTabChange}
          onAskChat={onAskChat}
          onCleared={onCleared}
          onEditInventoryLocation={actions.editInventoryLocation}
          onBulkListingsCreated={actions.bulkCreated}
          chat={chatNode}
        />
      </Suspense>
    </ErrorBoundary>
  );
});
