import type { ListingReviewTab } from "../components/ListingReviewTabs";
import type { ConversationSummary, ListingResponse } from "../api/types";
import { isListingJson } from "../components/chat/assistantDisplay";

export interface OpenListing {
  id: string;
  reviewTab: ListingReviewTab;
  /** The editor's tab: "general" or a marketplace id. */
  editTab: string;
  nonce: number;
  queuedMessage: string | null;
}

export type OpenListingAction =
  | { type: "open"; id: string; tab?: string }
  | { type: "close"; id: string }
  | { type: "review"; id: string; value: ListingReviewTab }
  | { type: "tab"; id: string; value: string }
  | { type: "message"; id: string; value: string | null }
  | { type: "clear"; id: string };

export function initialOpenListing(id: string | null): OpenListing | null {
  return id ? openListingReducer(null, { type: "open", id }) : null;
}

function onTab(state: OpenListing, tab: string): OpenListing {
  return { ...state, editTab: tab, reviewTab: tab === "general" ? "input" : "forms" };
}

/* One listing is open at a time; opening another replaces it. Opening on a
   tab, as the inventory table's marketplace cells do, moves the editor there. */
export function openListingReducer(state: OpenListing | null, action: OpenListingAction): OpenListing | null {
  if (action.type === "open") {
    const next = state?.id === action.id
      ? state
      : { id: action.id, reviewTab: "input" as const, editTab: "general", nonce: 0, queuedMessage: null };
    return action.tab ? onTab(next, action.tab) : next;
  }
  if (state?.id !== action.id) return state;
  if (action.type === "close") return null;
  if (action.type === "review") return { ...state, reviewTab: action.value };
  if (action.type === "tab") return onTab(state, action.value);
  if (action.type === "message") return { ...state, queuedMessage: action.value };
  return { ...state, nonce: state.nonce + 1, reviewTab: "input", editTab: "general", queuedMessage: null };
}

/* A link that opens this listing; the app reads ?listing= on load. */
export function listingHref(id: string): string {
  return `?${new URLSearchParams({ listing: id })}`;
}

/* Cmd/Ctrl/Shift/Alt or a non-primary button: let the browser handle the link. */
export function isBrowserLinkClick(event: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }): boolean {
  return event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
}

/**
 * The phone pane a listing opens on. A listing that already has copy (live,
 * sold, priced, or with a saved title and description) opens on its form;
 * chat's Generate would overwrite that copy. A draft still waiting to be
 * generated opens on chat, where Generate is the next step.
 */
export function phonePaneOnOpen(
  row: Pick<ConversationSummary, "status" | "price"> | undefined,
  saved: Pick<ListingResponse, "listing"> | undefined,
): "workspace" | "editor" {
  if (row && row.status !== "draft") return "editor";
  if ((row?.price ?? 0) > 0) return "editor";
  if (saved?.listing && isListingJson(JSON.stringify(saved.listing))) return "editor";
  return "workspace";
}
