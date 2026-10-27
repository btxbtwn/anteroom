import { createContext, useContext, type ReactNode } from "react";

/**
 * Whether this open listing is the one on screen.
 *
 * The open listing stays mounted while another view is on screen, so a draft
 * in the composer or the editor is not thrown away. Its photos and its
 * refresh timers stop.
 * Absent provider means active, so a screen rendered on its own still refreshes.
 */
const ListingSessionContext = createContext(true);

export function ListingSession({ active, children }: { active: boolean; children: ReactNode }) {
  return <ListingSessionContext.Provider value={active}>{children}</ListingSessionContext.Provider>;
}

export function useListingSessionActive(): boolean {
  return useContext(ListingSessionContext);
}
