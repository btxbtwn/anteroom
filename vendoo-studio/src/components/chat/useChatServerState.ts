import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { activityPollMs, BUSY_POLL_MS, LIVE_POLL_MS, pollMs } from "../../api/polling";
import { getLive, refreshListingQueries } from "./liveChatStore";

/** The server's view of the listing, polled faster while anything is running. */
export function useChatServerState(convId: string, localBusy: boolean) {
  const queryClient = useQueryClient();

  // The server's view of the listing: background field fills, saves, syncs and
  // Vendoo jobs keep running after a chat stream ends and can still post here.
  const { data: activity } = useQuery({
    queryKey: ["activity", convId],
    queryFn: () => api.conversations.activity(convId),
    refetchInterval: (query) =>
      activityPollMs(getLive(convId).streaming || getLive(convId).generating || Boolean(query.state.data?.busy)),
  });
  const serverBusy = Boolean(activity?.busy);
  const busy = localBusy || serverBusy;

  const { data: messages, isLoading } = useQuery({
    queryKey: ["messages", convId],
    queryFn: () => api.conversations.messages(convId),
    refetchInterval: busy ? () => pollMs(LIVE_POLL_MS) : false,
  });

  const { data: photos } = useQuery({
    queryKey: ["photos", convId],
    queryFn: () => api.conversations.photos(convId),
  });

  const { data: listing } = useQuery({
    queryKey: ["listing", convId],
    queryFn: () => api.listings.get(convId),
    refetchInterval: busy ? () => pollMs(BUSY_POLL_MS) : false,
  });

  const { data: jobs } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.jobs.list(),
    refetchInterval: busy ? () => pollMs(BUSY_POLL_MS) : false,
  });

  // Anything posted in the background shows up as soon as activity notices it.
  const lastMessageId = messages?.length ? messages[messages.length - 1].id : null;
  useEffect(() => {
    if (!activity || !messages) return;
    if (activity.message_count === messages.length && activity.last_message_id === lastMessageId) return;
    void queryClient.invalidateQueries({ queryKey: ["messages", convId] });
  }, [activity, messages, lastMessageId, convId, queryClient]);

  // Background work that finishes on its own may have changed the listing too.
  const wasServerBusyRef = useRef(serverBusy);
  useEffect(() => {
    if (wasServerBusyRef.current && !serverBusy) void refreshListingQueries(queryClient, convId);
    wasServerBusyRef.current = serverBusy;
  }, [serverBusy, convId, queryClient]);

  const activeProbe = (jobs || []).find(
    (j) =>
      j.conversation_id === convId
      && j.mode === "schema_probe"
      && ["queued", "awaiting_extension", "dispatched"].includes(String(j.status || "")),
  );

  return { activity, messages, isLoading, photos, listing, activeProbe, busy };
}
