import type { ListingReviewTab } from "../components/ListingReviewTabs";

export interface OpenListing {
  id: string;
  reviewTab: ListingReviewTab;
  nonce: number;
  queuedMessage: string | null;
}

export type OpenListingAction =
  | { type: "open"; id: string }
  | { type: "close"; id: string }
  | { type: "review"; id: string; value: ListingReviewTab }
  | { type: "message"; id: string; value: string | null }
  | { type: "clear"; id: string };

export function initialOpenListing(id: string | null): OpenListing | null {
  return id ? openListingReducer(null, { type: "open", id }) : null;
}

/* One listing is open at a time; opening another replaces it. */
export function openListingReducer(state: OpenListing | null, action: OpenListingAction): OpenListing | null {
  if (action.type === "open") {
    return state?.id === action.id ? state : { id: action.id, reviewTab: "input", nonce: 0, queuedMessage: null };
  }
  if (state?.id !== action.id) return state;
  if (action.type === "close") return null;
  if (action.type === "review") return { ...state, reviewTab: action.value };
  if (action.type === "message") return { ...state, queuedMessage: action.value };
  return { ...state, nonce: state.nonce + 1, reviewTab: "input", queuedMessage: null };
}
