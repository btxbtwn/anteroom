import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { clearLegacySettledExpanded, readLegacySettledExpanded } from "./listingShelves";

/**
 * Whether the Settled shelf is open. The choice lives in the UI settings; a
 * value left in localStorage by older builds is carried over once.
 */
export function useSettledShelf(uiPrefs: { settled_shelf_expanded: boolean } | undefined) {
  const queryClient = useQueryClient();
  const legacySettled = readLegacySettledExpanded();
  const [settledExpanded, setSettledExpanded] = useState(legacySettled ?? true);
  const migratedSettledRef = useRef(false);

  useEffect(() => {
    if (!uiPrefs) return;
    if (!migratedSettledRef.current) {
      migratedSettledRef.current = true;
      const legacy = readLegacySettledExpanded();
      if (legacy !== null) {
        clearLegacySettledExpanded();
        if (legacy !== uiPrefs.settled_shelf_expanded) {
          setSettledExpanded(legacy);
          void api.settings
            .setUi({ settled_shelf_expanded: legacy })
            .then(() => {
              queryClient.invalidateQueries({ queryKey: ["settings-ui"] });
            })
            .catch(() => {
              migratedSettledRef.current = false;
            });
          return;
        }
      }
    }
    setSettledExpanded(uiPrefs.settled_shelf_expanded);
  }, [uiPrefs, queryClient]);

  const toggleSettledShelf = useCallback(() => {
    setSettledExpanded((value) => {
      const next = !value;
      void api.settings
        .setUi({ settled_shelf_expanded: next })
        .then(() => {
          queryClient.invalidateQueries({ queryKey: ["settings-ui"] });
        })
        .catch(() => {
          /* keep in-memory toggle if disk write fails */
        });
      return next;
    });
  }, [queryClient]);

  return { settledExpanded, toggleSettledShelf };
}
