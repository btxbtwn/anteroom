import type { QueryClient } from "@tanstack/react-query";
import { api } from "./client";

// Only reads without side effects belong here. In particular, GET listing
// normalizes saved fields, so it starts on click, never on hover or focus.
export function prefetchListingReads(client: QueryClient, id: string) {
  return Promise.all([
    client.prefetchQuery({ queryKey: ["conversation", id], queryFn: () => api.conversations.get(id), retry: false }),
    client.prefetchQuery({ queryKey: ["photos", id], queryFn: () => api.conversations.photos(id), retry: false }),
    client.prefetchQuery({ queryKey: ["messages", id], queryFn: () => api.conversations.messages(id), retry: false }),
  ]);
}

export function prefetchAssistantThread(client: QueryClient, id: string) {
  return client.prefetchQuery({
    queryKey: ["assistant", "messages", id], queryFn: () => api.assistant.messages(id), retry: false,
  });
}
