import { createContext, useContext, type ReactNode } from "react";

/**
 * Whether this open listing is the one on screen.
 *
 * Hidden listing tabs stay mounted so a draft in the composer or the editor
 * is not thrown away. Their photos and their refresh timers do not.
 * Absent provider means active, so a screen rendered on its own still refreshes.
 */
const ListingSessionContext = createContext(true);

export function ListingSession({ active, children }: { active: boolean; children: ReactNode }) {
  return <ListingSessionContext.Provider value={active}>{children}</ListingSessionContext.Provider>;
}

export function useListingSessionActive(): boolean {
  return useContext(ListingSessionContext);
}
