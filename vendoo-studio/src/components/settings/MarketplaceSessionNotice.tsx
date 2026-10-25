import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import {
  EXTENSION_SESSION_MARKETS,
  EXTENSION_SESSION_NAMES,
  sessionIsExpiring,
  sessionNotification,
  type ExtensionSessionState,
} from "../../api/marketplaceSessions";
import { EXTENSION_STATUS_POLL_MS, pollMs } from "../../api/polling";
import { stackedThreadToast } from "../ui/toastHelpers";
import { toastManager } from "../ui/toast";

/** Tells the seller when a Chrome marketplace sign-in expires, once per expiry. */
export function MarketplaceSessionNotice({ onSignIn }: { onSignIn: () => void }) {
  const seen = useRef(new Set<string>());
  const previous = useRef<Partial<Record<string, ExtensionSessionState>>>({});
  const open = useRef(onSignIn);
  useEffect(() => {
    open.current = onSignIn;
  }, [onSignIn]);
  const extension = useQuery({
    queryKey: ["extension-status"],
    queryFn: api.extension.status,
    refetchInterval: () => pollMs(EXTENSION_STATUS_POLL_MS),
  });
  const chromeReady = Boolean(extension.data?.connected);
  const sessions = useQuery({
    queryKey: ["marketplace-sessions"],
    queryFn: api.marketplaceSessions.status,
    enabled: chromeReady,
    retry: false,
    refetchInterval: chromeReady ? 60_000 : false,
  });

  useEffect(() => {
    const data = sessions.data?.sessions;
    if (!data) return;
    for (const id of EXTENSION_SESSION_MARKETS) {
      const session = data[id];
      if (!session) continue;
      if (session.state === "signed_in" && !sessionIsExpiring(session)) {
        for (const key of seen.current) {
          if (key.startsWith(`${id}:`)) seen.current.delete(key);
        }
      }
      const alert = sessionNotification(id, EXTENSION_SESSION_NAMES[id], session, previous.current[id]);
      previous.current[id] = session.state;
      if (!alert || seen.current.has(alert.key)) continue;
      seen.current.add(alert.key);
      toastManager.add(stackedThreadToast({
        type: "warning",
        title: alert.title,
        description: alert.description,
        actionVariant: "outline",
        actionProps: { children: "Sign in", onClick: () => open.current() },
      }));
    }
  }, [sessions.data]);

  return null;
}
