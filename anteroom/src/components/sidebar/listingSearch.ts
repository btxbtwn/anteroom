import { useSyncExternalStore } from "react";

/**
 * The inventory search box's text. The sidebar, the phone search bar and the
 * inventory table share it, but the workspace around them does not read it,
 * so typing re-renders only the lists it filters, not the open listing or chat.
 */
let query = "";
const listeners = new Set<() => void>();

export function setListingSearch(value: string): void {
  if (value === query) return;
  query = value;
  listeners.forEach((listener) => listener());
}

export function getListingSearch(): string {
  return query;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useListingSearch(): string {
  return useSyncExternalStore(subscribe, getListingSearch, getListingSearch);
}
