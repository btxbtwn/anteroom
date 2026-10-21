import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";

export const USAGE_LIMITS_QUERY_KEY = ["usage-limits"];

/**
 * Every provider's usage. The server caches each read for a minute, so opening
 * the page often costs nothing; Refresh asks it to read again.
 */
export function useUsageLimits() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: USAGE_LIMITS_QUERY_KEY,
    queryFn: () => api.settings.usageLimits(),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    setRefreshing(true);
    try {
      const fresh = await api.settings.usageLimits(true);
      queryClient.setQueryData(USAGE_LIMITS_QUERY_KEY, fresh);
    } finally {
      setRefreshing(false);
    }
  };
  return { ...query, refreshing: refreshing || query.isFetching, refresh };
}
