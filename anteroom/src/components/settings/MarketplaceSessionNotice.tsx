import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { useWorkspaceRuntime } from "../../app/runtime";
import {
  EXTENSION_SESSION_MARKETS,
  EXTENSION_SESSION_NAMES,
  sessionIsExpiring,
  sessionNotification,
  type ExtensionSessionMarket,
  type ExtensionSessionState,
} from "../../api/marketplaceSessions";
import { EXTENSION_STATUS_POLL_MS, pollMs } from "../../api/polling";
import { stackedThreadToast } from "../ui/toastHelpers";
import { toastManager } from "../ui/toast";

const REMEMBERED_SIGN_INS = "chrome-marketplace-sign-ins";

function rememberedSignIns(): Set<string> {
  try {
    const parsed = JSON.parse(localStorage.getItem(REMEMBERED_SIGN_INS) || "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

function rememberSignIn(id: string) {
  const remembered = rememberedSignIns();
  if (remembered.has(id)) return;
  remembered.add(id);
  localStorage.setItem(REMEMBERED_SIGN_INS, JSON.stringify([...remembered]));
}

/** Tells the seller when a Chrome marketplace sign-in has ended, and opens that shop. */
export function MarketplaceSessionNotice() {
  const { hosted, account } = useWorkspaceRuntime();
  const queryClient = useQueryClient();
  const seen = useRef(new Set<string>());
  const previous = useRef<Partial<Record<string, ExtensionSessionState>>>({});
  const connect = useMutation({
    mutationFn: async ({ marketplace, cloud }: { marketplace: ExtensionSessionMarket; cloud: boolean }) => {
      if (cloud && hosted && account) {
        const { token } = await api.cloudSessions.connectToken(marketplace);
        const { cloudExtension } = await import("../../api/cloudExtension");
        const res = await cloudExtension(account).connectCloudSession(marketplace, token);
        if (!res.ok) throw new Error(res.error || `Open Settings → Connections in Chrome to reconnect ${EXTENSION_SESSION_NAMES[marketplace]} in Cloud.`);
        return;
      }
      await api.marketplaceSessions.connect(marketplace);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-sessions"] });
    },
    onError: (error: Error) => {
      toastManager.add(stackedThreadToast({
        type: "error",
        title: "Could not open the sign-in",
        description: error.message,
      }));
    },
  });
  const signIn = useRef(connect.mutate);
  useEffect(() => {
    signIn.current = connect.mutate;
  }, [connect.mutate]);
  const extension = useQuery({
    queryKey: ["extension-status"],
    queryFn: api.extension.status,
    enabled: !hosted,
    refetchInterval: () => pollMs(EXTENSION_STATUS_POLL_MS),
  });
  const chromeReady = hosted || Boolean(extension.data?.connected);
  const sessions = useQuery({
    queryKey: ["marketplace-sessions"],
    queryFn: api.marketplaceSessions.status,
    enabled: chromeReady,
    retry: false,
    refetchInterval: chromeReady ? () => pollMs(60_000) : false,
  });

  useEffect(() => {
    const data = sessions.data?.sessions;
    if (!data) return;
    for (const id of EXTENSION_SESSION_MARKETS) {
      const session = data[id];
      if (!session) continue;
      if (session.state === "signed_in") rememberSignIn(id);
      if (session.state === "signed_in" && !sessionIsExpiring(session)) {
        for (const key of seen.current) {
          if (key.startsWith(`${id}:`)) seen.current.delete(key);
        }
      }
      const alert = sessionNotification(
        id,
        EXTENSION_SESSION_NAMES[id],
        session,
        previous.current[id],
        rememberedSignIns().has(id),
      );
      previous.current[id] = session.state;
      if (!alert || seen.current.has(alert.key)) continue;
      seen.current.add(alert.key);
      const isCloud = session.mode === "cloud";
      toastManager.add(stackedThreadToast({
        type: "warning",
        title: alert.title,
        description: alert.description,
        timeout: 0,
        actionVariant: "outline",
        actionProps: { children: isCloud ? "Reconnect" : "Sign in", onClick: () => signIn.current({ marketplace: id, cloud: isCloud }) },
      }));
    }
  }, [sessions.data]);

  return null;
}
